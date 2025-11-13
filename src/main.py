import asyncio
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, ChatMemberUpdatedFilter, MEMBER, KICKED
from aiogram.enums.chat_type import ChatType

from sqlalchemy.orm import Session

from database import engine, SessionLocal
from models import Base, User, InviteLink, InvitationLog
from config import BOT_TOKEN, CHAT_ID

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Create database tables
Base.metadata.create_all(bind=engine)


def get_or_create_user(session: Session, telegram_user: types.User):
    """Gets a user from the database or creates a new one if they don't exist."""
    user = session.query(User).filter(User.telegram_id == telegram_user.id).first()
    if not user:
        user = User(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
            last_name=telegram_user.last_name,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    # Update user info if it has changed
    elif user.username != telegram_user.username or user.first_name != telegram_user.first_name or user.last_name != telegram_user.last_name:
        user.username = telegram_user.username
        user.first_name = telegram_user.first_name
        user.last_name = telegram_user.last_name
        session.commit()
    return user


@dp.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def send_welcome(message: types.Message):
    """Handles the /start command."""
    with SessionLocal() as session:
        get_or_create_user(session, message.from_user)
    await message.reply("Hi!\nI'm Invite Bot.\nSend me /invite to get a one-time invite link for the main chat.")


@dp.message(Command("info"))
async def send_info(message: types.Message):
    """Handles the /info command."""
    try:
        with open("info.txt", "r", encoding="utf-8") as f:
            info_text = f.read()
        await message.reply(info_text)
    except FileNotFoundError:
        logging.error("info.txt not found.")
        await message.reply("Information is currently unavailable.")


@dp.message(Command("invite"), F.chat.type == ChatType.PRIVATE)
async def invite(message: types.Message):
    """Handles the /invite command in a private chat."""
    logging.info(f"Received /invite from user {message.from_user.id} ({message.from_user.username})")
    if not CHAT_ID:
        logging.warning("CHAT_ID is not configured.")
        await message.reply("The bot is not configured with a target chat. Please contact the administrator.")
        return

    try:
        logging.info(f"Checking membership for user {message.from_user.id} in chat {CHAT_ID}")
        chat_member = await bot.get_chat_member(chat_id=CHAT_ID, user_id=message.from_user.id)
        logging.info(f"User {message.from_user.id} has status: {chat_member.status}")
        
        if chat_member.status not in ["member", "administrator", "creator"]:
            await message.reply("You must be a member of the group to create an invite link.")
            logging.warning(f"User {message.from_user.id} is not a member of the group. Status: {chat_member.status}")
            return
            
    except Exception as e:
        logging.error(f"Could not check chat member status: {e}", exc_info=True)
        await message.reply("I couldn't verify if you are in the group. Make sure I have the correct permissions and am an administrator in the chat.")
        return

    logging.info(f"User {message.from_user.id} is a member. Proceeding to link generation.")
    with SessionLocal() as session:
        inviter = get_or_create_user(session, message.from_user)
        logging.info(f"Database user object: {inviter.id}")

        # Check for existing active, unexpired links
        now = datetime.utcnow()
        active_link = session.query(InviteLink).filter(
            InviteLink.inviter_id == inviter.id,
            InviteLink.is_active == True,
            InviteLink.expires_at > now
        ).first()

        if active_link:
            time_left = (active_link.expires_at - now).seconds
            logging.info(f"User {message.from_user.id} already has an active link.")
            await message.reply(
                f"You already have an active invite link. It will expire in {time_left // 60} minutes and {time_left % 60} seconds.\n"
                f"{active_link.link}"
            )
            return

        # Create a new invite link
        logging.info(f"No active link found for user {message.from_user.id}. Creating a new one.")
        try:
            expires = datetime.utcnow() + timedelta(minutes=5)
            invite_link = await bot.create_chat_invite_link(
                chat_id=CHAT_ID,
                expire_date=expires,
                member_limit=1
            )

            new_link = InviteLink(
                link=invite_link.invite_link,
                inviter_id=inviter.id,
                expires_at=expires
            )
            session.add(new_link)
            session.commit()
            logging.info(f"Successfully created and saved new link for user {message.from_user.id}")
            await message.reply(f"Here is your one-time invite link. It is valid for 5 minutes:\n{invite_link.invite_link}")
        except Exception as e:
            logging.error(f"Error creating invite link: {e}", exc_info=True)
            await message.reply("I need to be an administrator in the target chat to create invite links.")

@dp.message(F.text.regexp(r'^@(\w{5,32})$'), F.chat.type == ChatType.PRIVATE)
async def who_invited(message: types.Message):
    """Handles admin requests to find out who invited a user."""
    logging.info(f"Received who_invited request from user {message.from_user.id} ({message.from_user.username}) in private chat.")

    if not CHAT_ID:
        logging.warning("CHAT_ID is not configured for who_invited command.")
        await message.reply("The bot is not configured with a target chat. Please contact the administrator.")
        return

    try:
        member = await bot.get_chat_member(chat_id=CHAT_ID, user_id=message.from_user.id)
        if member.status not in ["administrator", "creator"]:
            logging.warning(f"Non-admin user {message.from_user.id} tried to use who_invited in private chat.")
            await message.reply("You must be an administrator of the main group to use this command.")
            return
    except Exception as e:
        logging.error(f"Could not check admin status for who_invited: {e}", exc_info=True)
        await message.reply("I couldn't verify your admin status. Make sure I have the correct permissions in the main group.")
        return

    username_to_check = message.text.lstrip('@')

    with SessionLocal() as session:
        # Find the user in our database by username
        invitee = session.query(User).filter(User.username == username_to_check).first()

        if not invitee:
            await message.reply(f"I have no record of a user with the username @{username_to_check}.")
            return

        # Find the invitation log for this user
        log_entry = session.query(InvitationLog).filter(InvitationLog.invitee_id == invitee.id).first()

        if not log_entry:
            await message.reply(f"I have a record of @{username_to_check}, but I don't know who invited them.")
            return

        # Find the inviter
        inviter = session.query(User).filter(User.id == log_entry.inviter_id).first()

        if not inviter:
            # This should be rare, but good to handle
            await message.reply(f"I know @{username_to_check} was invited, but I can't find the inviter's data.")
            return
        
        inviter_display = f"@{inviter.username}" if inviter.username else f"{inviter.first_name}"
        await message.reply(f"User @{username_to_check} was invited by {inviter_display}.")


@dp.chat_member(ChatMemberUpdatedFilter(member_status_changed=(KICKED | MEMBER)))
async def on_new_chat_member(update: types.ChatMemberUpdated):
    """Handles new members joining the chat."""
    # Ensure we are in the correct chat
    if str(update.chat.id) != CHAT_ID:
        return

    if update.new_chat_member.status == "member":
        logging.info(f"New member {update.new_chat_member.user.username} joined chat {update.chat.id}")
        with SessionLocal() as session:
            invitee = get_or_create_user(session, update.new_chat_member.user)
            
            if update.invite_link:
                link_str = update.invite_link.invite_link
                logging.info(f"Join via invite link: {link_str}")
                
                # Find the link in the DB
                invite_link = session.query(InviteLink).filter(InviteLink.link == link_str).first()

                if invite_link and invite_link.is_active:
                    # Check if the link has expired
                    if invite_link.expires_at < datetime.utcnow():
                        invite_link.is_active = False
                        session.commit()
                        logging.warning(f"User {invitee.username} tried to join with expired link {link_str}")
                        return

                    inviter = session.query(User).filter(User.id == invite_link.inviter_id).first()
                    
                    # Log the invitation
                    log_entry = InvitationLog(
                        inviter_id=inviter.id,
                        invitee_id=invitee.id,
                        invite_link_id=invite_link.id
                    )
                    session.add(log_entry)
                    
                    # Deactivate the link
                    invite_link.is_active = False
                    invite_link.used_at = datetime.utcnow()
                    session.commit()
                    
                    if inviter:
                        try:
                            invitee_display = f"@{invitee.username}" if invitee.username else invitee.first_name
                            await bot.send_message(inviter.telegram_id, f"User {invitee_display} has joined using your invite link.")
                        except Exception as e:
                            logging.error(f"Could not notify inviter: {e}")
                    
                    logging.info(f"User {invitee.username} joined using link from {inviter.username}")
                else:
                    logging.warning(f"Invite link {link_str} not found in DB or is inactive.")


async def main():
    """Starts the bot."""
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, ChatMemberUpdatedFilter, MEMBER, KICKED
from aiogram.enums.chat_type import ChatType
from aiogram.enums.content_type import ContentType

from sqlalchemy.orm import Session

from database import engine, SessionLocal
from models import Base, User, InviteLink, InvitationLog
from config import BOT_TOKEN, CHAT_ID, SOURCE_TOPIC_ID, DESTINATION_TOPIC_ID

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
    """Handles the /invite command in a private chat.
    
    Allows regular users to generate one link.
    Allows admins to generate multiple links via `/invite <number>`.
    """
    logging.info(f"Received /invite from user {message.from_user.id} ({message.from_user.username})")
    
    MAX_LINKS_PER_REQUEST = 20  # Set a reasonable limit

    if not CHAT_ID:
        logging.warning("CHAT_ID is not configured.")
        await message.reply("The bot is not configured with a target chat. Please contact the administrator.")
        return

    # --- Check if user is a member of the group ---
    try:
        member = await bot.get_chat_member(chat_id=CHAT_ID, user_id=message.from_user.id)
        if member.status not in ["member", "administrator", "creator"]:
            await message.reply("You must be a member of the group to create an invite link.")
            return
    except Exception as e:
        logging.error(f"Could not check chat member status: {e}", exc_info=True)
        await message.reply("I couldn't verify if you are in the group. Make sure I have the correct permissions.")
        return

    # --- Parse arguments ---
    args = message.text.split()
    num_to_generate = 1
    is_mass_request = False

    if len(args) > 1:
        try:
            num_to_generate = int(args[1])
            is_mass_request = True
        except ValueError:
            await message.reply("Please provide a valid number of links to generate.")
            return

    # --- Handle mass generation for admins ---
    if is_mass_request:
        if member.status not in ["administrator", "creator"]:
            await message.reply("Only administrators can generate multiple links at once.")
            return
        
        if num_to_generate <= 0:
            await message.reply("Please provide a positive number.")
            return

        if num_to_generate > MAX_LINKS_PER_REQUEST:
            await message.reply(f"You can request a maximum of {MAX_LINKS_PER_REQUEST} links at a time. Generating {MAX_LINKS_PER_REQUEST} links.")
            num_to_generate = MAX_LINKS_PER_REQUEST
        
        generated_links = []
        with SessionLocal() as session:
            inviter = get_or_create_user(session, message.from_user)
            expires = datetime.utcnow() + timedelta(minutes=5)
            
            await message.reply(f"Generating {num_to_generate} links, please wait...")

            for i in range(num_to_generate):
                try:
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
                    generated_links.append(invite_link.invite_link)
                except Exception as e:
                    logging.error(f"Error creating one of the mass invite links: {e}")
                    await message.reply(f"An error occurred after generating {i} links. Please try again.")
                    return
            
            session.commit()

        if generated_links:
            response_text = "Here are your one-time invite links (valid for 5 minutes):\n\n" + "\n".join(generated_links)
            await message.reply(response_text)
        return

    # --- Handle single link generation for regular users ---
    with SessionLocal() as session:
        inviter = get_or_create_user(session, message.from_user)
        now = datetime.utcnow()
        active_link = session.query(InviteLink).filter(
            InviteLink.inviter_id == inviter.id,
            InviteLink.is_active == True,
            InviteLink.expires_at > now
        ).first()

        if active_link:
            time_left = (active_link.expires_at - now).seconds
            await message.reply(
                f"You already have an active invite link. It will expire in {time_left // 60} minutes and {time_left % 60} seconds.\n"
                f"{active_link.link}"
            )
            return

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


# This handler will delete system messages about new/left members, etc.
SERVICE_MESSAGE_TYPES = {
    ContentType.NEW_CHAT_MEMBERS,
    ContentType.LEFT_CHAT_MEMBER,
    ContentType.NEW_CHAT_TITLE,
    ContentType.NEW_CHAT_PHOTO,
    ContentType.DELETE_CHAT_PHOTO,
    ContentType.PINNED_MESSAGE,
}

@dp.message(F.content_type.in_(SERVICE_MESSAGE_TYPES))
async def delete_service_messages(message: types.Message):
    """Deletes service messages from the chat."""
    try:
        await message.delete()
        logging.info(f"Deleted a service message of type {message.content_type}.")
    except Exception as e:
        logging.error(f"Could not delete service message: {e}")


@dp.message(F.chat.type.in_({'group', 'supergroup'}))
async def topic_message_handler(message: types.Message):
    """
    Handles messages in topics.
    Forwards messages from a source topic to a destination topic.
    """
    # Ensure all configs are set and the message is in the correct chat
    if not all([CHAT_ID, SOURCE_TOPIC_ID, DESTINATION_TOPIC_ID]):
        logging.warning("One or more required environment variables (CHAT_ID, SOURCE_TOPIC_ID, DESTINATION_TOPIC_ID) are missing. Aborting forward.")
        return

    if str(message.chat.id) != CHAT_ID:
        logging.warning(f"Message from chat {message.chat.id} does not match configured CHAT_ID {CHAT_ID}. Aborting forward.")
        return

    # Check if the message is from a real user and in the source topic
    if not message.from_user.is_bot and str(message.message_thread_id) == SOURCE_TOPIC_ID:
        try:
            await bot.forward_message(
                chat_id=CHAT_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=int(DESTINATION_TOPIC_ID)
            )
            logging.info(f"Successfully forwarded message {message.message_id} to topic {DESTINATION_TOPIC_ID}")
        except Exception as e:
            logging.error(f"Could not forward message: {e}", exc_info=True)



async def main():
    """Starts the bot."""
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

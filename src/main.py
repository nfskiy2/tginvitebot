import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from sqlalchemy.orm import Session
from database import engine, SessionLocal
from models import Base, User, InviteLink, InvitationLog
from config import BOT_TOKEN
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Create database tables
Base.metadata.create_all(bind=engine)

def get_or_create_user(session: Session, telegram_user: types.User):
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
    return user

@dp.message(CommandStart())
async def send_welcome(message: types.Message):
    """
    This handler will be called when user sends `/start` command
    """
    with SessionLocal() as session:
        get_or_create_user(session, message.from_user)
    await message.reply("Hi!\nI'm Invite Bot.\nUse /invite to get a one-time invite link.")

@dp.message(Command("invite"))
async def invite(message: types.Message):
    """
    This handler will be called when user sends `/invite` command
    """
    with SessionLocal() as session:
        inviter = get_or_create_user(session, message.from_user)
        
        # Expire old links
        active_links = session.query(InviteLink).filter(InviteLink.inviter_id == inviter.id, InviteLink.is_active == True).all()
        for link in active_links:
            try:
                await bot.revoke_chat_invite_link(message.chat.id, link.link)
            except Exception as e:
                logging.error(f"Could not revoke old link: {e}")
            link.is_active = False
        session.commit()

        # Create a new invite link
        try:
            invite_link = await bot.create_chat_invite_link(message.chat.id, member_limit=1)
            new_link = InviteLink(
                link=invite_link.invite_link,
                inviter_id=inviter.id,
            )
            session.add(new_link)
            session.commit()
            await message.reply(f"Here is your one-time invite link: {invite_link.invite_link}")
        except Exception as e:
            await message.reply("I need to be an administrator in this chat to create invite links.")
            logging.error(f"Error creating invite link: {e}")


@dp.chat_member()
async def on_new_chat_member(update: types.ChatMemberUpdated):
    """
    This handler will be called when a new user joins the chat.
    """
    if update.new_chat_member.status == "member":
        with SessionLocal() as session:
            invitee = get_or_create_user(session, update.new_chat_member.user)
            
            if update.invite_link:
                link_str = update.invite_link.invite_link
                invite_link = session.query(InviteLink).filter(InviteLink.link == link_str).first()

                if invite_link and invite_link.is_active:
                    inviter = session.query(User).filter(User.id == invite_link.inviter_id).first()
                    
                    log_entry = InvitationLog(
                        inviter_id=inviter.id,
                        invitee_id=invitee.id,
                        invite_link_id=invite_link.id
                    )
                    session.add(log_entry)
                    
                    invite_link.is_active = False
                    invite_link.used_at = datetime.utcnow()
                    
                    session.commit()
                    
                    if inviter:
                        try:
                            await bot.send_message(inviter.telegram_id, f"User {invitee.first_name} (@{invitee.username}) has joined using your invite link.")
                        except Exception as e:
                            logging.error(f"Could not notify inviter: {e}")
                    logging.info(f"User {invitee.username} joined using link from {inviter.username}")


async def main():
    """
    This function will be called when the bot starts.
    """
    # Start polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

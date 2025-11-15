import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/database.db")

# For message forwarding
SOURCE_TOPIC_ID = os.getenv("SOURCE_TOPIC_ID")
DESTINATION_TOPIC_ID = os.getenv("DESTINATION_TOPIC_ID")

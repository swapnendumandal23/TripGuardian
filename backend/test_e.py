import asyncio
from bot import handle_message, trip_store
from telegram import Update, Message, Chat, User
from telegram.ext import ContextTypes
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestE")

class MockContext:
    class bot:
        @staticmethod
        async def send_message(chat_id, text, parse_mode=None):
            pass
        @staticmethod
        async def send_chat_action(chat_id, action):
            pass

class MockMessage:
    def __init__(self, text, user_id=11112222):
        self.text = text
        self.chat = Chat(id=user_id, type="private")
        self.from_user = User(id=user_id, first_name="Test", is_bot=False)
        self.location = None

class MockLocationMessage:
    def __init__(self, lat, lon, user_id=11112222):
        self.text = None
        self.chat = Chat(id=user_id, type="private")
        self.from_user = User(id=user_id, first_name="Test", is_bot=False)
        class Loc:
            latitude = lat
            longitude = lon
        self.location = Loc()

class MockUpdate:
    def __init__(self, chat_id, text):
        class Chat:
            id = chat_id
            type = "private"
        class User:
            id = chat_id
            first_name = "TestUser"
        class Message:
            def __init__(self, t):
                self.text = t
                self.chat = Chat()
                self.from_user = User()
        self.message = Message(text)
        self.effective_chat = Chat()
        self.effective_user = User()

async def simulate_message(chat_id, text):
    print(f"\nUser: \"{text}\"")
    update = MockUpdate(chat_id, text)
    context = MockContext()
    
    import bot
    original_safe_reply = bot.safe_reply
    
    output = []
    async def mock_safe_reply(msg, text, **kwargs):
        output.append(text)
        
    bot.safe_reply = mock_safe_reply
    
    await bot.handle_message(update, context)
    
    bot.safe_reply = original_safe_reply
    print(f"Bot:\n{output[0] if output else 'No response'}")

async def run_test():
    user_id = 11112222
    trip_store.clear_trip(user_id)
    trip_store.save_trip(user_id, "Goa", "Test itinerary")
    trip_store.update_demo_location(user_id, 15.5523, 73.7517, is_demo=True)
    
    print("=== TEST E: VISITED PLACE EXCLUSION ===")
    print("Location: Baga Beach (15.5523, 73.7517)\n")

    await simulate_message(user_id, "Find seafood nearby.")
    await simulate_message(user_id, "I've already visited Hidden Seafood Gem.")
    await simulate_message(user_id, "Suggest something else nearby.")
    
    print("\n--- Context State ---")
    print(f"Visited: {trip_store.get_visited_places(user_id)}")

if __name__ == "__main__":
    asyncio.run(run_test())

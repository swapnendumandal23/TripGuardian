import asyncio
import sys
sys.path.append('.')

from bot import handle_message
from trip_store import trip_store
from unittest.mock import MagicMock, AsyncMock
import time

class MockUser:
    def __init__(self, chat_id):
        self.id = chat_id

class MockMessage:
    def __init__(self, text):
        self.text = text

class MockUpdate:
    def __init__(self, chat_id, text):
        self.effective_chat = MockUser(chat_id)
        self.message = MockMessage(text)

class MockBot:
    def __init__(self):
        self.send_chat_action = AsyncMock()

class MockContext:
    def __init__(self):
        self.bot = MockBot()

async def simulate_message(chat_id, text):
    print(f"\nUser: \"{text}\"")
    update = MockUpdate(chat_id, text)
    context = MockContext()
    
    # We will just patch safe_reply to intercept the output
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
    chat_id = 11112222
    trip_store.clear_trip(chat_id)
    trip_store.save_trip(chat_id, "Goa", "Test itinerary")
    trip_store.update_demo_location(chat_id, 15.5523, 73.7517, is_demo=True)
    
    print("=== TEST D: NATURAL LANGUAGE CONVERSATION ===")
    print("Location: Baga Beach (15.5523, 73.7517)\n")

    await simulate_message(chat_id, "I'm hungry, what can I eat nearby?")
    time.sleep(3)
    
    await simulate_message(chat_id, "Something cheap.")
    time.sleep(3)
    
    await simulate_message(chat_id, "Preferably seafood.")
    time.sleep(3)
    
    await simulate_message(chat_id, "I've already visited Baga Beach.")
    time.sleep(3)
    
    await simulate_message(chat_id, "Suggest something else nearby.")
    time.sleep(3)
    
    await simulate_message(chat_id, "Which one is closest?")
    time.sleep(3)
    
    print("\n--- Context State ---")
    ctx = trip_store.get_conversation_context(chat_id)
    visited = trip_store.get_visited_places(chat_id)
    print(f"Context: {ctx}")
    print(f"Visited: {visited}")

if __name__ == "__main__":
    asyncio.run(run_test())

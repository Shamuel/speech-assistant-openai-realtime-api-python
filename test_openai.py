import os
import json
import asyncio
import websockets
from dotenv import load_dotenv
import ssl

load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

print(websockets.__version__)

async def test_openai_connection():
    print("Testing connection to OpenAI Realtime API...")
    try:
        # Create connection options
        ssl_context = ssl.create_default_context()
        
        # Create a connection with the headers
        async with websockets.connect(
            'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17',
            additional_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            },
            ssl=ssl_context
        ) as openai_ws:
            print("Successfully connected to OpenAI")
            
            # Send a simple session update
            session_update = {
                "type": "session.update",
                "session": {
                    "voice": "alloy",
                    "instructions": "You are a helpful assistant.",
                    "modalities": ["text"],
                }
            }
            await openai_ws.send(json.dumps(session_update))
            print("Sent session update")
            
            # Wait for a response
            response = await openai_ws.recv()
            print(f"Received from OpenAI: {response}")
            
            # Send a simple text message
            message = {
                "type": "conversation.item.create",
                "item": {
                    "role": "user",
                    "type": "message",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Hello, are you working?"
                        }
                    ]
                }
            }
            await openai_ws.send(json.dumps(message))
            print("Sent test message")
            
            # Wait for message confirmation
            response = await openai_ws.recv()
            print(f"Message confirmation: {response}")
            
            # Explicitly request a response from the assistant
            response_request = {
                "type": "response.create"
            }
            await openai_ws.send(json.dumps(response_request))
            print("Requested assistant response")
            
            # Wait for responses
            print("Waiting for assistant response...")
            for i in range(15):
                try:
                    response = await openai_ws.recv()
                    parsed = json.loads(response)
                    print(f"Received message {i+1}: {parsed['type']}")
                    
                    # Print full response for important events
                    if parsed['type'] == 'conversation.item.created' and 'item' in parsed and parsed['item'].get('role') == 'assistant':
                        print(f"Assistant response: {json.dumps(parsed, indent=2)}")
                    else:
                        print(f"Full message: {response}")
                    
                except websockets.exceptions.ConnectionClosedOK:
                    print("Connection closed normally")
                    break
                except Exception as e:
                    print(f"Error receiving message: {e}")
                    break
                
            print("Test completed successfully")
            
    except Exception as e:
        print(f"Error connecting to OpenAI: {e}")
        import traceback
        traceback.print_exc()

# Run the test
if __name__ == "__main__":
    asyncio.run(test_openai_connection())

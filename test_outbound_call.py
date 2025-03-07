import os
from twilio.rest import Client
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get Twilio credentials from environment variables
account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
phone_number_from = os.getenv("TWILIO_PHONE_NUMBER")

# Initialize Twilio client
client = Client(account_sid, auth_token)

def make_test_call(to_number):
    """
    Make a test outbound call using Twilio.
    
    Args:
        to_number (str): The phone number to call (E.164 format, e.g., +1XXXXXXXXXX)
    
    Returns:
        The SID of the created call
    """
    try:
        call = client.calls.create(
            from_=phone_number_from,
            to=to_number,
            url="http://demo.twilio.com/docs/voice.xml"  # Twilio's demo TwiML
        )
        print(f"Call initiated! Call SID: {call.sid}")
        return call.sid
    except Exception as e:
        print(f"Error making call: {e}")
        return None

if __name__ == "__main__":
    # Ask for the destination phone number
    to_number = input("Enter the phone number to call (in E.164 format, e.g., +1XXXXXXXXXX): ")
    
    # Validate input (basic check)
    if not to_number.startswith("+"):
        print("Phone number should be in E.164 format (starting with +)")
    else:
        make_test_call(to_number) 
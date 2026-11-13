import logging
from dotenv import load_dotenv
from furhat_realtime_api import FurhatClient
from google import genai
from google.genai import errors, types
import argparse

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Furhat robot IP address")
    parser.add_argument("--auth_key", type=str, default=None, help="Authentication key for Realtime API")
    parser.add_argument("--model", type=str, default="gemini-3.5-flash", help="Gemini model ID")
    args = parser.parse_args()

    load_dotenv(override=True)

    # Reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment (.env)
    gemini = genai.Client()

    furhat = FurhatClient(host=args.host, auth_key=args.auth_key)
    furhat.set_logging_level(logging.INFO)

    try:
        furhat.connect()
    except Exception as e:
        print(f"Failed to connect to Furhat on {args.host}.")
        exit(0)

    config = types.GenerateContentConfig(
        system_instruction=(
            "You are a friendly robot looking for a nice little chat. "
            "Keep replies short (one or two sentences), since they will be spoken aloud. "
            "Do not use markdown, lists or emojis."
        ),
        max_output_tokens=1024,
    )

    def turn(role, text):
        # Gemini uses the roles "user" and "model" (not "assistant")
        return types.Content(role=role, parts=[types.Part(text=text)])

    # Start the conversation with a user message
    contents = [turn("user", "Hi!")]
    robot_utt = "Hello, I am Furhat. How are you today?"

    furhat.request_attend_user()  # This will default to attending the closest user

    while True:
        print("Robot: ", robot_utt)
        contents.append(turn("model", robot_utt))
        furhat.request_speak_text(robot_utt)
        user_utt = furhat.request_listen_start()
        print("User: ", user_utt)

        if not user_utt or not user_utt.strip():
            # Don't send empty turns to the API, ask again instead
            robot_utt = "Sorry, I didn't catch that. Could you say it again?"
            continue

        contents.append(turn("user", user_utt))

        try:
            response = gemini.models.generate_content(
                model=args.model,
                contents=contents,
                config=config,
            )
        except errors.APIError as e:
            print(f"Gemini API error: {e}")
            contents.pop()  # drop the unanswered user turn so roles keep alternating
            robot_utt = "Sorry, I had trouble thinking just now. Could you repeat that?"
            continue

        # response.text is None if the reply was blocked by safety filters or empty
        if not response.text:
            contents.pop()
            robot_utt = "I'm sorry, I can't help with that. Let's talk about something else."
            continue

        robot_utt = response.text

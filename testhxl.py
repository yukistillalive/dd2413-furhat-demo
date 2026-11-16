
from furhat_realtime_api import FurhatClient

# Your physical Furhat robot
ROBOT_IP = "192.168.1.114"

def main():
    furhat = FurhatClient(ROBOT_IP)

    try:
        # 1. Connect to the robot
        furhat.connect()
        print("Connected to Furhat!")

        # 2. Make Furhat speak
        furhat.request_speak_text(
            "Hello! I am Furhat. Nice to meet you.",
            wait=True
        )

        # 3. Make Furhat smile
        furhat.request_gesture_start(
            "Smile",
            intensity=0.8,
            duration=2.0,
            wait=True
        )

        # 4. Ask a question
        furhat.request_speak_text(
            "What is your name?",
            wait=True
        )

        # 5. Listen to the user
        furhat.request_listen_config(
            languages=["en-US"]
        )

        answer = furhat.request_listen_start(
            no_speech_timeout=10.0
        )

        print("Recognized:", answer)

        # 6. Respond to the user
        if answer:
            furhat.request_speak_text(
                f"Nice to meet you! I heard you say {answer}.",
                wait=True
            )
        else:
            furhat.request_speak_text(
                "Sorry, I didn't hear you.",
                wait=True
            )

    finally:
        # 7. Close the connection
        furhat.disconnect()
        print("Disconnected.")

if __name__ == "__main__":
    main()

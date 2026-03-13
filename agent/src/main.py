import os
import signal
import sys
import time

def handle_exit(signum, frame):
    print(f"SIGTERM received ({signum}). Saving state and exiting...")
    # TODO: Implement checkpoint_session()
    sys.exit(0)

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGTERM, handle_exit)
signal.signal(signal.SIGINT, handle_exit)

def main():
    session_id = os.getenv("CLAW_SESSION_ID", "default-session")
    gateway_url = os.getenv("LLM_GATEWAY_URL", "http://llm-gateway:8000")
    
    print(f"ContainerClaw Agent started.")
    print(f"Session ID: {session_id}")
    print(f"LLM Gateway: {gateway_url}")
    
    # Placeholder for the main control loop
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()

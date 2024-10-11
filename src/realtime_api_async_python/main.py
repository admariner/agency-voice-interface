# src/realtime_api_async_python/main.py
import asyncio
import json
import logging
import os
import websockets
from websockets.exceptions import ConnectionClosedError

from realtime_api_async_python.config import (
    SESSION_INSTRUCTIONS,
    SILENCE_THRESHOLD,
    PREFIX_PADDING_MS,
    SILENCE_DURATION_MS,
)
from realtime_api_async_python.microphone import AsyncMicrophone
from realtime_api_async_python.utils import (
    log_ws_event,
    base64_encode_audio,
)
from realtime_api_async_python.websocket_handler import process_ws_messages


# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)


async def realtime_api():
    while True:
        try:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                logging.error("Please set the OPENAI_API_KEY in your .env file.")
                return

            exit_event = asyncio.Event()

            url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "OpenAI-Beta": "realtime=v1",
            }

            mic = AsyncMicrophone()

            async with websockets.connect(url, extra_headers=headers) as websocket:
                logging.info("Connected to the server.")

                # Initialize the session with voice capabilities and tools
                session_update = {
                    "type": "session.update",
                    "session": {
                        "modalities": ["text", "audio"],
                        "instructions": SESSION_INSTRUCTIONS,
                        "voice": "alloy",
                        "input_audio_format": "pcm16",
                        "output_audio_format": "pcm16",
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": SILENCE_THRESHOLD,
                            "prefix_padding_ms": PREFIX_PADDING_MS,
                            "silence_duration_ms": SILENCE_DURATION_MS,
                        },
                        "tools": [
                            {
                                "type": "function",
                                "name": "get_current_time",
                                "description": "Returns the current time.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {},
                                    "required": [],
                                },
                            },
                            {
                                "type": "function",
                                "name": "get_random_number",
                                "description": "Returns a random number between 1 and 100.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {},
                                    "required": [],
                                },
                            },
                            {
                                "type": "function",
                                "name": "open_browser",
                                "description": "Opens a browser tab with the best-fitting URL based on the user's prompt.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "prompt": {
                                            "type": "string",
                                            "description": "The user's prompt to determine which URL to open.",
                                        },
                                    },
                                    "required": ["prompt"],
                                },
                            },
                            {
                                "type": "function",
                                "name": "create_file",
                                "description": "Generates content for a new file based on the user's prompt and file name.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "file_name": {
                                            "type": "string",
                                            "description": "The name of the file to create.",
                                        },
                                        "prompt": {
                                            "type": "string",
                                            "description": "The user's prompt to generate the file content.",
                                        },
                                    },
                                    "required": ["file_name", "prompt"],
                                },
                            },
                            {
                                "type": "function",
                                "name": "update_file",
                                "description": "Updates a file based on the user's prompt.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "prompt": {
                                            "type": "string",
                                            "description": "The user's prompt describing the updates to the file.",
                                        },
                                        "model": {
                                            "type": "string",
                                            "enum": [
                                                "state_of_the_art_model",
                                                "reasoning_model",
                                                "base_model",
                                                "fast_model",
                                            ],
                                            "description": "The model to use for generating the updates. Default to 'base_model' if not specified.",
                                        },
                                    },
                                    "required": ["prompt"],  # 'model' is optional
                                },
                            },
                            {
                                "type": "function",
                                "name": "delete_file",
                                "description": "Deletes a file based on the user's prompt.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "prompt": {
                                            "type": "string",
                                            "description": "The user's prompt describing the file to delete.",
                                        },
                                        "force_delete": {
                                            "type": "boolean",
                                            "description": "Whether to force delete the file without confirmation. Default to 'false' if not specified.",
                                        },
                                    },
                                    "required": ["prompt"],
                                },
                            },
                            {
                                "type": "function",
                                "name": "delegate_task_to_developer",
                                "description": "Delegates a task to the Developer agent in the Agency Swarm.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "task_description": {
                                            "type": "string",
                                            "description": "Description of the task to delegate.",
                                        },
                                    },
                                    "required": ["task_description"],
                                },
                            },
                            {
                                "type": "function",
                                "name": "assign_task_to_virtual_assistant",
                                "description": "Assigns a task to the Virtual Assistant agent in the Agency Swarm.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "task_description": {
                                            "type": "string",
                                            "description": "Description of the task to assign.",
                                        },
                                    },
                                    "required": ["task_description"],
                                },
                            },
                        ],
                    },
                }
                log_ws_event("outgoing", session_update)
                await websocket.send(json.dumps(session_update))

                ws_task = asyncio.create_task(process_ws_messages(websocket, mic))

                logging.info(
                    "Conversation started. Speak freely, and the assistant will respond."
                )
                mic.start_recording()
                logging.info("Recording started. Listening for speech...")

                try:
                    while not exit_event.is_set():
                        await asyncio.sleep(
                            0.1
                        )  # Small delay to accumulate some audio data
                        if not mic.is_receiving:
                            audio_data = mic.get_audio_data()
                            if audio_data:
                                base64_audio = base64_encode_audio(audio_data)
                                if base64_audio:
                                    audio_event = {
                                        "type": "input_audio_buffer.append",
                                        "audio": base64_audio,
                                    }
                                    log_ws_event("outgoing", audio_event)
                                    await websocket.send(json.dumps(audio_event))
                                else:
                                    logging.debug("No audio data to send")
                except KeyboardInterrupt:
                    logging.info("Keyboard interrupt received. Closing the connection.")
                finally:
                    exit_event.set()
                    mic.stop_recording()
                    mic.close()
                    await websocket.close()

                # Wait for the WebSocket processing task to complete
                await ws_task

            # If execution reaches here without exceptions, exit the loop
            break
        except ConnectionClosedError as e:
            if "keepalive ping timeout" in str(e):
                logging.warning(
                    "WebSocket connection lost due to keepalive ping timeout. Reconnecting..."
                )
                await asyncio.sleep(1)  # Wait before reconnecting
                continue  # Retry the connection
            logging.exception("WebSocket connection closed unexpectedly.")
            break  # Exit the loop on other connection errors
        except Exception as e:
            logging.exception(f"An unexpected error occurred: {e}")
            break  # Exit the loop on unexpected exceptions
        finally:
            if "mic" in locals():
                mic.stop_recording()
                mic.close()
            if "websocket" in locals():
                await websocket.close()


async def main_async():
    await realtime_api()


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logging.info("Program terminated by user")
    except Exception as e:
        logging.exception(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    print("Press Ctrl+C to exit the program.")
    main()
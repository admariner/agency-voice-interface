# src/realtime_api_async_python/websocket_handler.py
import json
import logging
import base64
import time
import websockets
from realtime_api_async_python.functions import FUNCTION_MAP
from realtime_api_async_python.utils import log_ws_event, log_runtime
from realtime_api_async_python.audio import play_audio


async def process_ws_messages(websocket, mic):
    assistant_reply = ""
    audio_chunks = []
    function_call = None
    function_call_args = ""
    response_start_time = None

    while True:
        try:
            message = await websocket.recv()
            event = json.loads(message)
            log_ws_event("incoming", event)

            event_type = event.get("type")

            if event_type == "response.created":
                mic.start_receiving()
            elif event_type == "response.output_item.added":
                item = event.get("item", {})
                if item.get("type") == "function_call":
                    function_call = item
                    function_call_args = ""
            elif event_type == "response.function_call_arguments.delta":
                function_call_args += event.get("delta", "")
            elif event_type == "response.function_call_arguments.done":
                if function_call:
                    function_name = function_call.get("name")
                    call_id = function_call.get("call_id")
                    try:
                        args = (
                            json.loads(function_call_args) if function_call_args else {}
                        )
                    except json.JSONDecodeError:
                        args = {}
                    if function_name in FUNCTION_MAP:
                        logging.info(
                            f"🛠️ Calling function: {function_name} with args: {args}"
                        )
                        result = await FUNCTION_MAP[function_name](**args)
                        logging.info(f"🛠️ Function call result: {result}")
                    else:
                        result = {"error": f"Function '{function_name}' not found."}
                    function_call_output = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(result),
                        },
                    }
                    log_ws_event("outgoing", function_call_output)
                    await websocket.send(json.dumps(function_call_output))
                    await websocket.send(json.dumps({"type": "response.create"}))
                    function_call = None
                    function_call_args = ""
            elif event_type == "response.text.delta":
                assistant_reply += event.get("delta", "")
                print(
                    f"Assistant: {event.get('delta', '')}",
                    end="",
                    flush=True,
                )
            elif event_type == "response.audio.delta":
                audio_chunks.append(base64.b64decode(event["delta"]))
            elif event_type == "response.done":
                if response_start_time is not None:
                    response_duration = time.perf_counter() - response_start_time
                    log_runtime("realtime_api_response", response_duration)
                    response_start_time = None

                logging.info("Assistant response complete.")
                if audio_chunks:
                    audio_data = b"".join(audio_chunks)
                    logging.info(
                        f"Sending {len(audio_data)} bytes of audio data to play_audio()"
                    )
                    await play_audio(audio_data)
                    logging.info("Finished play_audio()")
                assistant_reply = ""
                audio_chunks = []
                logging.info("Calling stop_receiving()")
                mic.stop_receiving()
            elif event_type == "rate_limits.updated":
                mic.is_recording = True
                logging.info("Resumed recording after rate_limits.updated")
            elif event_type == "error":
                error_message = event.get("error", {}).get("message", "")
                logging.error(f"Error: {error_message}")
                if "buffer is empty" in error_message:
                    logging.info(
                        "Received 'buffer is empty' error, no audio data sent."
                    )
                    continue
                elif "Conversation already has an active response" in error_message:
                    logging.info(
                        "Received 'active response' error, adjusting response flow."
                    )
                    continue
                else:
                    logging.error(f"Unhandled error: {error_message}")
                    break
            elif event_type == "input_audio_buffer.speech_started":
                logging.info("Speech detected, listening...")
            elif event_type == "input_audio_buffer.speech_stopped":
                mic.stop_recording()
                logging.info("Speech ended, processing...")

                # start the response timer, on send
                response_start_time = time.perf_counter()
                await websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))

        except websockets.ConnectionClosed:
            logging.warning("WebSocket connection closed")
            break
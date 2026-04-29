import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))
import asyncio
import random
import logging
import json
import io
from collections import deque
from typing import List, Optional, Dict, Any

import aiohttp
from pysip import *
from pysip.sip_account import SipAccount
from pysip.amd.amd import AnswringMachineDetector
from pysip.filters import CallState


logging.basicConfig(
     level=logging.INFO,
     format="%(asctime)s [%(levelname)s] %(message)s",
     handlers=[
          logging.FileHandler("database/logs/sip_call.log"),
          logging.StreamHandler()
     ]
)


def load_config(
     config_path: str
) -> dict:
     with open(
          config_path,
          "r"
     ) as file:
          return json.load(file)


async def send_webhook_update(
     webhook_url: str,
     data: dict
) -> None:
     try:
          async with aiohttp.ClientSession() as session:
               async with session.post(
                    webhook_url,
                    json=data,
                    timeout=10
               ) as resp:
                    if resp.status != 200:
                         logging.warning(
                              f"Webhook response not OK: {resp.status}"
                         )
     except Exception as e:
          logging.error(
               f"Failed to send webhook: {e}"
          )


async def make_sip_call(
     phone_number: str,
     account: SipAccount,
     first_script: str,
     second_script: str,
     metadata: Optional[Dict] = None,
     webhook_url: Optional[str] = None,
     fallback_msg: str = "ok bro wtv wtv ",
     timeout_v1: int = 60,
     call_results: Dict[str, Any] = None,
     first_script_audio_param: io.BytesIO = None,
     second_script_audio_param: io.BytesIO = None
) -> None:
     if call_results is None:
          call_results = {}

     meta = metadata or {}
     meta_str = f"[META-DATA: {json.dumps(meta)}]"

     # check if account is still registered before making call
     if not account.sip_core.is_running.is_set():
          logging.warning(f"[SKIP] Account not registered, cannot call {phone_number} {meta_str}")
          return

     call = account.make_call(
          phone_number
     )

     if hasattr(
     call.call_handler,
     "amd_detector"
):
      call.call_handler.amd_detector = None

     # Track if call was ever answered (to distinguish from connection failures)
     call_was_answered = False
     last_error_reason = None
     
     # Set up a callback to capture hangup reasons
     async def capture_hangup_reason(reason: str):
          nonlocal last_error_reason
          last_error_reason = reason
          logging.debug(f"[{phone_number}] Hangup reason captured: {reason}")
     
     # Register callback to capture hangup reasons
     call._register_callback("hanged_up_cb", capture_hangup_reason)

     call_task = asyncio.create_task(
          call.start()
     )

     # wait a moment for the call to be fully established
     await asyncio.sleep(2)
     

          # wait for call to be answered before playing script
     logging.info(f"[{phone_number}] Waiting for call to be answered... {meta_str}")
     
     # wait for call state to be ANSWERED
     max_wait_time = 45  # wait up to 45 seconds for answer
     wait_start = asyncio.get_event_loop().time()
     
     # Monitor call state transitions
     last_state = call.call_state
     state_check_count = 0
     
     while call.call_state != CallState.ANSWERED:
          current_state = call.call_state
          
          # Check if call was answered
          if current_state == CallState.ANSWERED:
               call_was_answered = True
               break
          
          # Check if call failed (Service Unavailable, authentication, Forbidden, etc.)
          if current_state == CallState.FAILED:
               # Give a brief moment for the callback to capture the error reason
               await asyncio.sleep(0.2)
               
               # Use captured error reason if available, otherwise use generic
               error_msg = last_error_reason or "Call failed - connection error"
               
               # Check dialogue state to get more context
               try:
                    if hasattr(call, 'dialogue') and hasattr(call.dialogue, 'state'):
                         dialogue_state = str(call.dialogue.state)
                         # If dialogue was confirmed, call was answered
                         if "CONFIRMED" in dialogue_state:
                              call_was_answered = True
                              error_msg = "Call answered but then failed/hung up"
                         elif "TERMINATED" in dialogue_state and not call_was_answered:
                              # Call failed before being answered - use the captured reason
                              if not last_error_reason:
                                   error_msg = "Call failed before answer - connection error"
               except Exception as e:
                    logging.debug(f"Could not extract dialogue state: {e}")
               
               # Normalize error messages for retry logic
               error_msg_lower = error_msg.lower()
               if "forbidden" in error_msg_lower or "invalid" in error_msg_lower:
                    error_msg = "Forbidden/Invalid - call rejected by server"
               elif "service unavailable" in error_msg_lower:
                    error_msg = "Service Unavailable - server overloaded"
               elif "unable to authenticate" in error_msg_lower or "authenticate" in error_msg_lower:
                    error_msg = "Unable to authenticate - authentication failed"
               
               # Log the failure with available details
               logging.warning(f"[{phone_number}] Call failed: {current_state} - {error_msg} {meta_str}")
               
               # Store the error message (will be checked for retry)
               call_results[phone_number] = {
                    "pressed_1": False,
                    "error": error_msg,
                    **meta
               }
               try:
                    if not call._is_call_stopped:
                         await call.stop("Call failed")
               except Exception:
                    pass
               return
          
          # Check if call ended (this usually means it was answered and hung up)
          if current_state == CallState.ENDED:
               # Give a moment for callback to capture reason
               await asyncio.sleep(0.2)
               
               # Check if call was answered before ending
               try:
                    if hasattr(call, 'dialogue') and hasattr(call.dialogue, 'state'):
                         dialogue_state = str(call.dialogue.state)
                         if "CONFIRMED" in dialogue_state:
                              call_was_answered = True
               except Exception:
                    pass
               
               # If call was answered, it's not a failure - just hung up
               if call_was_answered or last_error_reason == "Callee hanged up":
                    logging.info(f"[{phone_number}] Call was answered but hung up (not retryable) {meta_str}")
                    call_results[phone_number] = {
                         "pressed_1": False,
                         "error": "Call answered but hung up",
                         **meta
                    }
               else:
                    # Call ended without being answered - this is a failure
                    error_msg = last_error_reason or "Call ended without answer"
                    logging.warning(f"[{phone_number}] Call ended without answer: {error_msg} {meta_str}")
                    call_results[phone_number] = {
                         "pressed_1": False,
                         "error": error_msg,
                         **meta
                    }
               try:
                    if not call._is_call_stopped:
                         await call.stop("Call ended")
               except Exception:
                    pass
               return
          
          # Track state changes
          if current_state != last_state:
               logging.debug(f"[{phone_number}] Call state changed: {last_state} -> {current_state}")
               last_state = current_state
          
          # Timeout check
          if asyncio.get_event_loop().time() - wait_start > max_wait_time:
               logging.warning(f"[{phone_number}] Call not answered within {max_wait_time}s, hanging up {meta_str}")
               await call.stop("Call not answered")
               call_results[phone_number] = {
                    "pressed_1": False,
                    "error": "Call timeout - not answered",
                    **meta
               }
               return
          
          await asyncio.sleep(0.1)
          state_check_count += 1
     
     # If we get here, call was answered
     call_was_answered = True
     
     logging.info(f"[{phone_number}] Call answered, now playing first script {meta_str}")
     
     try:
          logging.info(f"[{phone_number}] Playing first script: '{first_script[:50]}...' {meta_str}")
          
          # Use pre-generated audio if available, otherwise generate on-demand
          if first_script_audio_param is not None:
               # Reset the audio stream position for reuse
               first_script_audio_param.seek(0)
               await call.call_handler.say_pre_generated(first_script_audio_param)
          else:
               await call.call_handler.say(first_script)
               
          logging.info(f"[{phone_number}] First script completed successfully {meta_str}")
          
          if call.call_state != CallState.ANSWERED:
               logging.warning(f"[{phone_number}] Call no longer answered before DTMF gathering {meta_str}")
               call_results[phone_number] = {
                    "pressed_1": False,
                    **meta
               }
               return
          
          dtmf = await call.call_handler.gather(
               length=1,
               timeout=10
          )
     except RuntimeError as e:
          logging.warning(
               f"[{phone_number}] DTMF failed: {e} {meta_str}"
          )
          # Wait a bit for the call to finish properly
          caller_id_switched_in_batch = False
          try:
               await asyncio.sleep(2)
               await call_task
          except Exception as e:
               logging.warning(f"[{phone_number}] Call task error: {e} {meta_str}")
          # Record result even if DTMF failed
          call_results[phone_number] = {
               "pressed_1": False,
               **meta
          }
          return

     if dtmf == "1":
          try:
               logging.info(
                    f"[SUCCESS] {phone_number} pressed 1, playing second script {meta_str}"
               )
               
               # Use pre-generated audio if available, otherwise generate on-demand
               if second_script_audio_param is not None:
                    # Reset the audio stream position for reuse
                    second_script_audio_param.seek(0)
                    await call.call_handler.say_pre_generated(second_script_audio_param)
               else:
                    await call.call_handler.say(second_script)
               
               logging.info(
                    f"[SUCCESS] {phone_number} second script started, waiting 12 seconds {meta_str}"
               )
               
               call_results[phone_number] = {
                    "pressed_1": True,
                    **meta
               }
               if webhook_url:
                    await send_webhook_update(
                         webhook_url,
                         {
                              "phone_number": phone_number,
                              "pressed_1": True,
                              **meta
                         }
                    )
               
               await asyncio.sleep(10)
               
               logging.info(
                    f"[SUCCESS] {phone_number} second script completed, hanging up {meta_str}"
               )
               
               try:
                    await call.stop("Call completed")
                    logging.info(
                         f"[{phone_number}] clean hangup after second script {meta_str}"
                    )
               except Exception as e:
                    logging.warning(
                         f"[{phone_number}] error on hangup: {e} {meta_str}"
                    )
          except RuntimeError as e:
               logging.warning(
                    f"[{phone_number}] failed second script: {e} {meta_str}"
               )
               try:
                    await call.stop("Call completed")
                    logging.info(
                         f"[{phone_number}] clean hangup after script failure {meta_str}"
                    )
               except Exception as e:
                    logging.warning(
                         f"[{phone_number}] error on hangup after script failure: {e} {meta_str}"
                    )
     else:
          logging.info(
               f"[NO INPUT] {phone_number} didn't press 1 {meta_str}"
          )
          call_results[phone_number] = {
               "pressed_1": False,
               **meta
          }
          
          try:
               await call.stop("Call completed - no input")
               logging.info(
                    f"[{phone_number}] clean hangup - no input {meta_str}"
               )
          except Exception as e:
               logging.warning(
                    f"[{phone_number}] error on hangup - no input: {e} {meta_str}"
               )

     # wait for call to complete (it should already be hung up)
     try:
          await call_task
     except Exception as e:
          logging.warning(f"[{phone_number}] Call task error: {e} {meta_str}")

     logging.info(
          f"[END] Finished with {phone_number} {meta_str}"
     )


async def run_campaign(
    phone_numbers: List[str],
    first_script: str,
    second_script: str,
    caller_id_pool_name: str = "Random 888",
    config_path: str = "database/config.json",
    metadata: Optional[Dict] = None,
    webhook_url: Optional[str] = None,
) -> int:
    """Run a dialing campaign with retries, throttling, and caller ID rotation."""

    fallback_msg = "Thank you for pressing 1. A representative will contact you shortly."
    concurrent_calls = 10
    timeout_v1 = 20
    call_results: Dict[str, Any] = {}
    
    # Track call statistics
    total_call_attempts = 0
    successful_calls = 0

    config = load_config(config_path)

    sip_users = config.get("sip_users", [])
    if not sip_users:
        logging.error("No SIP users found in config.")
        return 0

    sip_user = sip_users[0].get("username")
    sip_pass = sip_users[0].get("password")
    if not sip_user or not sip_pass:
        logging.error("SIP username/password missing.")
        return 0

    # Load caller ID pool - use legitimate numbers assigned to the account
    number_pool = config.get("number_pool", {})
    caller_ids = [cid for cid in (number_pool.get(caller_id_pool_name) or []) if cid]
    if not caller_ids:
        logging.error(f"No valid numbers found for pool: {caller_id_pool_name}")
        return 0
    
    logging.info(f"Using {len(caller_ids)} caller IDs from pool '{caller_id_pool_name}' - will rotate randomly per call")

    sip_domain = config.get("sip_domain")
    sip_connection_type = config.get("sip_connection_type", "UDP")
    sip_register_refresh = config.get("sip_register_refresh", 300)
    if not sip_domain:
        logging.error("sip_domain not found in config.json")
        return 0

    # Start with first caller ID from pool - will be changed per-call
    current_caller_id = random.choice(caller_ids)
    account = SipAccount(
        sip_user,
        sip_pass,
        sip_domain,
        connection_type=sip_connection_type,
        caller_id=current_caller_id,
        register_duration=sip_register_refresh,
    )

    logging.info(f"Attempting to register SIP account {sip_user}...")
    registration_successful = False
    max_registration_attempts = 3
    for attempt in range(max_registration_attempts):
        try:
            logging.info(f"SIP registration attempt {attempt + 1}...")
            is_registered = await account.register()
            if is_registered or account.sip_core.is_running.is_set():
                logging.info(
                    f"SIP account {sip_user} successfully registered on attempt {attempt + 1}!"
                )
                registration_successful = True
                break
            logging.warning(
                f"SIP registration attempt {attempt + 1} failed, retrying..."
            )
            if attempt < max_registration_attempts - 1:
                await asyncio.sleep(5)
        except Exception as exc:
            logging.error(
                f"SIP registration attempt {attempt + 1} failed with error: {exc}"
            )
            if attempt < max_registration_attempts - 1:
                await asyncio.sleep(5)

    if not registration_successful:
        logging.error(
            f"SIP registration failed for {sip_user} after {max_registration_attempts} attempts. Cannot proceed with calls."
        )
        return 0

    phone_numbers = list(set(phone_numbers))
    logging.info(
        f"SIP {sip_user} ready — {len(phone_numbers)} unique numbers to dial with provider-assigned random caller IDs."
    )
    logging.info("TTS audio will be generated on-demand during calls")

    first_script_audio: Optional[io.BytesIO] = None
    second_script_audio: Optional[io.BytesIO] = None

    # Load pre-recorded WAV files
    try:
        with open("first_script.wav", "rb") as f:
            first_script_audio = io.BytesIO(f.read())
        with open("second_script.wav", "rb") as f:
            second_script_audio = io.BytesIO(f.read())
        logging.info("Loaded pre-recorded WAV files successfully.")
    except FileNotFoundError as e:
        logging.error(f"WAV file not found: {e}. Please run gen_audio.py first.")
        return 0

    current_concurrent_calls = concurrent_calls
    sem = asyncio.Semaphore(current_concurrent_calls)
    auth_failure_counter = 0
    max_auth_failures = 5
    aborted_due_to_auth = False
    # Removed forbidden tracking since we're not doing caller ID rotation anymore

    async def limited_call(
        phone_number: str,
        first_script_audio_param: io.BytesIO = None,
        second_script_audio_param: io.BytesIO = None,
    ) -> None:
        nonlocal aborted_due_to_auth

        if aborted_due_to_auth:
            call_results[phone_number] = {
                "pressed_1": False,
                "error": "Campaign aborted",
                **(metadata or {}),
                "target_number": phone_number,
            }
            return

        async with sem:
            try:
                if not account.sip_core.is_running.is_set():
                    logging.error(
                        "SIP account lost registration. Aborting campaign to avoid repeated logins."
                    )
                    aborted_due_to_auth = True
                    call_results[phone_number] = {
                        "pressed_1": False,
                        "error": "SIP account lost registration",
                        **(metadata or {}),
                        "target_number": phone_number,
                    }
                    return

                # Rotate caller ID for each call to avoid provider blocking
                account.caller_id = random.choice(caller_ids)
                
                await asyncio.sleep(random.uniform(0.1, 0.5))
                logging.info(f"[START] Calling {phone_number} with caller ID {account.caller_id}")
                await make_sip_call(
                    phone_number,
                    account,
                    first_script,
                    second_script,
                    metadata={**(metadata or {}), "target_number": phone_number},
                    webhook_url=webhook_url,
                    fallback_msg=fallback_msg,
                    timeout_v1=timeout_v1,
                    call_results=call_results,
                    first_script_audio_param=first_script_audio_param,
                    second_script_audio_param=second_script_audio_param,
                )
            except Exception as exc:
                error_msg = str(exc)
                logging.error(f"[ERROR] Call failed for {phone_number}: {error_msg}")
                call_results[phone_number] = {
                    "pressed_1": False,
                    **(metadata or {}),
                    "target_number": phone_number,
                    "error": error_msg,
                }
                lowered = error_msg.lower()
                if "service unavailable" in lowered or "503" in error_msg or "rate limit" in lowered:
                    logging.warning(
                        f"[RATE LIMIT] Detected rate limiting for {phone_number}, calls may be throttled"
                    )
                if "authenticate" in lowered:
                    logging.warning(
                        f"[AUTH ERROR] Detected authentication error for {phone_number}."
                    )
            finally:
                result = call_results.get(phone_number)
                if result and result.get("error"):
                    err_lower = str(result["error"]).lower()
                    if "unable to authenticate" in err_lower:
                        await handle_authentication_failure(
                            f"{phone_number}: {result['error']}"
                        )

    async def handle_authentication_failure(reason: str) -> bool:
        nonlocal auth_failure_counter, aborted_due_to_auth

        auth_failure_counter += 1
        logging.warning(
            f"Authentication failure detected ({auth_failure_counter}/{max_auth_failures}): {reason}"
        )

        await asyncio.sleep(min(5 * auth_failure_counter, 15))

        if auth_failure_counter >= max_auth_failures:
            aborted_due_to_auth = True
            logging.error(
                "Exceeded authentication failure threshold. Campaign will abort to avoid repeated login attempts."
            )
            return False

        return True

    # Removed caller ID rotation functions - provider doesn't allow spoofing

    BATCH_SIZE = 20  # Reduced from 30 to be more conservative
    DELAY_BETWEEN_BATCHES = 25  # Increased from 20 to give more cooldown
    DELAY_WITHIN_BATCH = 2.0  # Increased from 1.5 to spread calls more

    batches = [
        phone_numbers[i : i + BATCH_SIZE]
        for i in range(0, len(phone_numbers), BATCH_SIZE)
    ]
    total_batches = len(batches)

    logging.info(
        f"Processing {len(phone_numbers)} numbers in {total_batches} batches of up to {BATCH_SIZE} numbers each"
    )
    logging.info(
        f"Concurrent calls per batch (initial): {current_concurrent_calls}, delay between batches: {DELAY_BETWEEN_BATCHES}s"
    )

    consecutive_failures = 0
    total_failures = 0
    total_attempts = 0
    failed_calls_retry: Dict[str, int] = {}
    max_retries_per_number = 3

    for batch_index, batch_numbers in enumerate(batches, start=1):
        if failed_calls_retry:
            retry_numbers = [
                number
                for number, retry_count in failed_calls_retry.items()
                if retry_count < max_retries_per_number
            ]
            if retry_numbers:
                logging.info(
                    f"Adding {len(retry_numbers)} failed calls from previous batches to batch {batch_index} for retry"
                )
                batch_numbers = retry_numbers + batch_numbers
                for number in retry_numbers:
                    failed_calls_retry.pop(number, None)

        logging.info(
            f"Starting batch {batch_index}/{total_batches} with {len(batch_numbers)} numbers"
        )

        if not account.sip_core.is_running.is_set():
            logging.warning("SIP registration lost! Stopping campaign to avoid re-login attempts.")
            aborted_due_to_auth = True
            break

        if consecutive_failures >= 5:
            extra_delay = 30
            logging.warning(
                f"Too many consecutive batch failures ({consecutive_failures}). Adding {extra_delay}s delay to avoid rate limiting..."
            )
            await asyncio.sleep(extra_delay)
            consecutive_failures = 0

        if total_attempts > 20 and (total_failures / max(total_attempts, 1)) >= 0.98:
            logging.error(
                f"CRITICAL: Failure rate is {total_failures}/{total_attempts} ({total_failures/total_attempts*100:.1f}%). Stopping campaign to avoid account issues."
            )
            logging.error("Almost all calls are being rejected. Please check:")
            logging.error("1. SIP account credentials and status")
            logging.error("2. SIP provider rate limits and account limits")
            logging.error("3. Account balance or calling restrictions")
            break

        batch_tasks: List[asyncio.Task] = []
        for index_in_batch, number in enumerate(batch_numbers):
            base_delay = DELAY_WITHIN_BATCH * (1 + consecutive_failures * 0.5)
            delay = index_in_batch * base_delay + random.uniform(0, base_delay)

            async def delayed_call(num: str, delay_time: float) -> None:
                if delay_time > 0:
                    await asyncio.sleep(delay_time)
                await limited_call(num, first_script_audio, second_script_audio)

            batch_tasks.append(asyncio.create_task(delayed_call(number, delay)))

        caller_id_switched_in_batch = False
        try:
            logging.info(
                f"Waiting for {len(batch_tasks)} calls in batch {batch_index} to complete..."
            )
            done, pending = await asyncio.wait(
                batch_tasks,
                timeout=300,
                return_when=asyncio.ALL_COMPLETED,
            )

            for task in done:
                try:
                    await task
                except Exception as exc:
                    logging.debug(f"Task exception: {exc}")

            batch_failures = 0
            batch_successes = 0

            for number in batch_numbers:
                total_attempts += 1
                if number in call_results:
                    result = call_results[number]
                    error_msg = str(result.get("error", ""))
                    err_lower = error_msg.lower()

                    is_retryable_error = (
                        result.get("error")
                        and "answered but hung up" not in err_lower
                        and "answered but then failed" not in err_lower
                    )

                    retryable_tokens = [
                        "service unavailable",
                        "unable to authenticate",
                        "internal server error",
                        "connection error",
                        "call failed",
                        "call timeout",
                        "call ended without answer",
                        "call not answered",
                        "forbidden",  # Now treat forbidden as retryable
                        "invalid",
                    ]
                    has_retryable_error = any(token in err_lower for token in retryable_tokens)

                    if is_retryable_error and has_retryable_error:
                        batch_failures += 1
                        total_failures += 1
                        retry_count = failed_calls_retry.get(number, 0)
                        if retry_count < max_retries_per_number:
                            failed_calls_retry[number] = retry_count + 1
                            logging.info(
                                f"[RETRY] Adding {number} to retry queue (attempt {retry_count + 1}/{max_retries_per_number}). Error: {error_msg[:100]}"
                            )
                        else:
                            logging.warning(
                                f"[NO RETRY] {number} exceeded max retries ({max_retries_per_number}), not retrying"
                            )
                    elif result.get("pressed_1") is not False or "error" not in result:
                        batch_successes += 1
                        if number in failed_calls_retry:
                            failed_calls_retry.pop(number)
                    else:
                        batch_failures += 1
                        total_failures += 1
                        retry_count = failed_calls_retry.get(number, 0)
                        if retry_count < max_retries_per_number:
                            failed_calls_retry[number] = retry_count + 1
                            logging.info(
                                f"[RETRY] Adding {number} to retry queue (attempt {retry_count + 1}/{max_retries_per_number}) - no result recorded"
                            )
                else:
                    batch_failures += 1
                    total_failures += 1
                    retry_count = failed_calls_retry.get(number, 0)
                    if retry_count < max_retries_per_number:
                        failed_calls_retry[number] = retry_count + 1
                        logging.info(
                            f"[RETRY] Adding {number} to retry queue (attempt {retry_count + 1}/{max_retries_per_number}) - no result recorded"
                        )

            failure_rate = batch_failures / len(batch_numbers) if batch_numbers else 0
            overall_failure_rate = total_failures / total_attempts if total_attempts > 0 else 0

            if failure_rate >= 0.8:
                consecutive_failures += 1
                logging.warning(
                    f"Batch {batch_index} had {batch_failures}/{len(batch_numbers)} failures ({failure_rate*100:.1f}% failure rate). Consecutive failure count: {consecutive_failures}"
                )
                logging.warning(
                    f"Overall failure rate: {total_failures}/{total_attempts} ({overall_failure_rate*100:.1f}%)"
                )
            elif failure_rate > 0.6:
                consecutive_failures += 1
                logging.warning(
                    f"Batch {batch_index} had {batch_failures}/{len(batch_numbers)} failures ({failure_rate*100:.1f}% failure rate). Consecutive failure count: {consecutive_failures}"
                )
            else:
                consecutive_failures = 0
                logging.info(
                    f"Batch {batch_index} had {batch_successes} successes, {batch_failures} failures"
                )

            logging.info(
                f"Batch {batch_index} completed: {len(done)} done, {len(pending)} pending, overall: {total_failures}/{total_attempts} failed"
            )

            for task in pending:
                logging.warning(f"Cancelling pending task in batch {batch_index}")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except Exception as exc:
            logging.error(f"Error in batch {batch_index}: {exc}")
            consecutive_failures += 1
            for task in batch_tasks:
                if not task.done():
                    task.cancel()

        if aborted_due_to_auth:
            break
            continue

        if batch_index < total_batches:
            batch_delay = DELAY_BETWEEN_BATCHES
            if consecutive_failures > 0:
                batch_delay = DELAY_BETWEEN_BATCHES * (1 + consecutive_failures * 0.5)
                logging.info(
                    f"Batch {batch_index} complete. Waiting {batch_delay:.1f}s before next batch (increased due to failures)..."
                )
            else:
                logging.info(
                    f"Batch {batch_index} complete. Waiting {batch_delay}s before next batch..."
                )
            await asyncio.sleep(batch_delay)
        else:
            logging.info(f"Final batch {batch_index} complete. No delay needed.")

    if aborted_due_to_auth:
        logging.error(
            "Campaign aborted after repeated authentication or registration-loss failures. Please verify SIP credentials and account status before retrying."
        )
    else:
        logging.info(f"All {total_batches} batches processed")

    if failed_calls_retry and not aborted_due_to_auth:
        remaining_retries = [
            number
            for number, retry_count in failed_calls_retry.items()
            if retry_count < max_retries_per_number
        ]
        if remaining_retries:
            logging.info(
                f"Processing {len(remaining_retries)} remaining failed calls as final retry batch..."
            )

            if not account.sip_core.is_running.is_set():
                logging.warning(
                    "SIP registration lost before final retry batch. Skipping retries to avoid new registrations."
                )
                remaining_retries = []

            if remaining_retries:
                retry_tasks: List[asyncio.Task] = []
                for index_in_retry, number in enumerate(remaining_retries):
                    delay = index_in_retry * DELAY_WITHIN_BATCH + random.uniform(
                        0, DELAY_WITHIN_BATCH
                    )

                    async def delayed_retry_call(num: str, delay_time: float) -> None:
                        if delay_time > 0:
                            await asyncio.sleep(delay_time)
                        await limited_call(num, first_script_audio, second_script_audio)

                    retry_tasks.append(asyncio.create_task(delayed_retry_call(number, delay)))

                try:
                    logging.info(
                        f"Waiting for {len(retry_tasks)} retry calls to complete..."
                    )
                    done, pending = await asyncio.wait(
                        retry_tasks,
                        timeout=300,
                        return_when=asyncio.ALL_COMPLETED,
                    )

                    for task in done:
                        try:
                            await task
                        except Exception as exc:
                            logging.debug(f"Retry task exception: {exc}")

                    for task in pending:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass

                    retry_successes = 0
                    retry_failures = 0
                    for number in remaining_retries:
                        if number in call_results and not call_results[number].get("error"):
                            retry_successes += 1
                        else:
                            retry_failures += 1

                    logging.info(
                        f"Final retry batch completed: {retry_successes} successes, {retry_failures} failures"
                    )
                except Exception as exc:
                    logging.error(f"Error in final retry batch: {exc}")

    await asyncio.sleep(5)

    if account.sip_core and account.sip_core.is_running.is_set():
        try:
            await account.unregister()
            logging.info(f"SIP account {sip_user} unregistered.")
        except Exception as exc:
            logging.warning(f"Failed to unregister SIP account {sip_user}: {exc}")
    else:
        logging.warning(f"SIP account {sip_user} was already disconnected.")

    logging.info(f"Campaign completed successfully for {len(phone_numbers)} numbers")
    logging.info(f"Total call_results entries: {len(call_results)}")

    pressed_1_count = sum(
        1 for result in call_results.values() if result.get("pressed_1", False)
    )

    logging.info(f"run_campaign: Returning pressed_1_count={pressed_1_count}")
    return pressed_1_count

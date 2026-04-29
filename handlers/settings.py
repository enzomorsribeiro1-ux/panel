import os, json
import asyncio

BASE = 'database/config.json'
TOKEN_KEY = 'token'
WEBHOOK_KEY = 'webhook'
APIURL_KEY = 'apiurl'
SETTINGS_FILE = './database/user_settings.json'

def load_token():
    config = manage_file(
        'load_config',
        BASE
    )
    return config.get(
        TOKEN_KEY
    )


def load_webhook():
    config = manage_file(
        'load_config',
        BASE
    )
    return config.get(
        WEBHOOK_KEY
    )

def asyncs_v1(
     async_func, 
     *args, 
     **kwargs
):
     try:
          loop = asyncio.get_event_loop()
     except RuntimeError:
          loop = asyncio.new_event_loop()
          asyncio.set_event_loop(loop)

     loop.run_until_complete(
          async_func(
               *args, 
               **kwargs
          )
     )


def load_apiurl():
    config = manage_file(
        'load_config',
        BASE
    )
    return config.get(
        APIURL_KEY
    )


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


def task_usersettings():
     if not os.path.exists(
          SETTINGS_FILE
     ):
          os.makedirs(
               os.path.dirname(SETTINGS_FILE),
               exist_ok=True
          )
          with open(
               SETTINGS_FILE,
               'w'
          ) as f:
               json.dump(
                    {},
                    f
               )
     with open(
          SETTINGS_FILE,
          'r'
     ) as f:
          settings = json.load(
               f
          )

     for user_id, user_settings in settings.items():
          if 'balance' not in user_settings:
               user_settings['balance'] = 0.0
     
     return settings

def task_saveusers(settings):
    with open(
        SETTINGS_FILE,
        'w'
    ) as f:
        json.dump(
            settings,
            f,
            indent=4  
        )

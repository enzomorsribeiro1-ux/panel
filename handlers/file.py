import re, os, requests, uuid, unicodedata

from telegram import Update
from telegram.ext import CallbackContext

def download_file(
     url,
     save_path
):
     response = requests.get(
          url,
          stream=True
     )
     if response.status_code == 200:
          with open(
               save_path,
               'wb'
          ) as file:
               file.write(
                    response.content
               )
          print(
          )
     else:
          raise Exception(
          )

def escape_markdown(
     text: str
) -> str:
     if not text:
          return ""

     return re.sub(
          r"([_\*\[\]\(\)~`>#\+\-=|{}.!])",
          r"\\\1",
          text
     )

def extract_num(
     file_path
):
     phone_numbers = []
     emails = []
     names = []

     # US phone pattern: (XXX) XXX-XXXX or XXX-XXX-XXXX
     us_phone_pattern = re.compile(
          r"\b(?:\+?1[-.\s]?|)?(?:\((\d{3})\)|(\d{3}))[-.\s]?(\d{3})[-.\s]?(\d{4})\b"
     )
     # Australian phone pattern: handles +61, 61, or 0 prefix formats
     # Matches: +61 4XX XXX XXX, +61 X XXXX XXXX, 04XX XXX XXX, 0X XXXX XXXX, 61 X XXXX XXXX
     au_phone_pattern = re.compile(
          r"\b(?:\+?61[-.\s]?|0)(\d{1,2})[-.\s]?(\d{4})[-.\s]?(\d{4})\b|\b(?:\+?61[-.\s]?|0)(4\d{2})[-.\s]?(\d{3})[-.\s]?(\d{3})\b"
     )
     email_pattern = re.compile(
          r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
     )

     _, ext = os.path.splitext(
          file_path
     )
     if ext.lower() != ".txt":
          print(
               "Only .txt files are supported."
          )
          return [], [], []

     with open(
          file_path,
          "r",
          encoding="utf-8"
     ) as file:
          for line in file:
               line = line.strip()

               line = unicodedata.normalize(
                    "NFKC",
                    line
               ).replace(
                    "\u200e", ""
               ).replace(
                    "\u202c", ""
               )

               emails_found = email_pattern.findall(
                    line
               )
               us_nums_found = us_phone_pattern.findall(
                    line
               )
               au_nums_found = au_phone_pattern.findall(
                    line
               )

               phone = None
               # Try Australian numbers first if pattern matches
               if au_nums_found:
                    # Use clean_number to handle Australian number formatting
                    phone = clean_number(line)
               elif us_nums_found:
                    match = us_nums_found[0]
                    # handle both (area) and area formats
                    area = match[0] if match[0] else match[1]  # first group (with parentheses) or second group (without)
                    mid = match[2]
                    last = match[3]
                    phone = f"+1{area}{mid}{last}"
               else:
                    # fallback: try clean_number on the entire line if regex didn't find anything
                    phone = clean_number(line)

               email = emails_found[0] if emails_found else None

               remaining_text = re.sub(
                    email_pattern,
                    "",
                    line
               )
               remaining_text = re.sub(
                    us_phone_pattern,
                    "",
                    remaining_text
               )
               remaining_text = re.sub(
                    au_phone_pattern,
                    "",
                    remaining_text
               ).strip()

               name = remaining_text if remaining_text else None

               if phone:
                    phone_numbers.append(
                         phone
                    )
               if email:
                    emails.append(
                         email
                    )
               if name:
                    names.append(
                         name
                    )

     return phone_numbers, emails, names

def clean_number(
     phone_number: str
) -> str:
     phone_number = phone_number.strip()
     
     # first, try to extract all digits
     digits = ''.join(filter(str.isdigit, phone_number))
     
     # Handle Australian numbers first
     # Australian mobile: 04XX XXX XXX (10 digits starting with 04) or +61 4XX XXX XXX
     # Australian landline: 0X XXXX XXXX (10 digits starting with 02, 03, 07, 08) or +61 X XXXX XXXX
     
     # If it already starts with +61, normalize it (remove spaces, dashes, etc.)
     if phone_number.lower().startswith('+61'):
          # Extract digits after +61
          au_digits = ''.join(filter(str.isdigit, phone_number[3:]))
          if len(au_digits) >= 8 and len(au_digits) <= 9:
               return '+61' + au_digits
     
     # If it's 11 digits starting with 61 (without +)
     if len(digits) == 11 and digits.startswith('61'):
          return '+' + digits
     
     # If it's 10 digits starting with 0 (Australian format without country code)
     if len(digits) == 10 and digits.startswith('0'):
          # Replace leading 0 with +61
          return '+61' + digits[1:]
     
     # If it's 9 digits starting with 4 (Australian mobile without 0 prefix)
     if len(digits) == 9 and digits.startswith('4'):
          return '+61' + digits
     
     # Handle US numbers
     # handle 10-digit numbers (add +1) - but only if not Australian format
     if len(digits) == 10 and not digits.startswith('0'):
          return '+1' + digits
     
     # handle 11-digit numbers starting with 1 (US)
     if len(digits) == 11 and digits.startswith('1'):
          return '+' + digits
     
     # try regex for US formatted numbers like (650) 535-0693
     us_phone_pattern = re.compile(r'\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})')
     match = us_phone_pattern.search(phone_number)
     
     if match:
          area, mid, last = match.groups()
          return f"+1{area}{mid}{last}"
     
     # Try Australian formatted numbers: 04XX XXX XXX or 0X XXXX XXXX
     au_phone_pattern = re.compile(r'(?:0?)(\d{1,2})[-.\s]?(\d{4})[-.\s]?(\d{4})')
     match = au_phone_pattern.search(phone_number)
     if match:
          # Check if it looks like Australian (starts with 0 or matches Australian patterns)
          if phone_number.strip().startswith('0') or (len(digits) == 10 and digits.startswith('0')):
               # Remove leading 0 and add +61
               au_digits = ''.join(filter(str.isdigit, phone_number))
               if au_digits.startswith('0'):
                    return '+61' + au_digits[1:]
     
     # No valid format found
     return None
     
def save_numbers(
     phone_numbers,
     output_path
):
     with open(
          output_path,
          'w'
     ) as file:
          for number in phone_numbers:
               file.write(
                    f"{number}\n"
               )

def readnow(
     url,
     download_path,
     output_path
):
     download_file(
          url,
          download_path
     )
     phone_numbers = extract_num(
          download_path
     )
     save_numbers(
          phone_numbers,
          output_path
     )
     print(
          f"amounr -> {len(phone_numbers)} & saved to ->: {output_path}"
     )

def chars_v1(
     text: str
) -> str:
    escape_chars = r'[_*[\]()~>#+={}.!\\-]'
    return re.sub(r'([_*\[\]()~>#+={}.!\\\-])', r'\\\1', text)

def chars_v5(
     text
):
     escape_chars = r'_ * [ ] ( ) ~ ` > # + = | { } !'.replace(
          " ", ""
     )  # IF WE OVER ESCAPE ANY OTHER SPECIAL CHARS IT WILL BREAK
     
     return ''.join(
          f'\\{char}' if char in escape_chars else char 
          for char in text
     ).replace(
          "-", "\\-"
     )


def chars_v2(
     text
):
     return text.replace(
          "\\", "\\\\"
     ).replace(
          ".", "\\."
     ).replace(
          "-", "\\-"
     ).replace(
          "_", "\\_"
     ).replace(
          "*", "\\*"
     ).replace(
          "[", "\\["
     ).replace(
          "]", "\\]"
     ).replace(
          "(", "\\("
     ).replace(
          ")", "\\)"
     ).replace(
          "~", "\\~"
     ).replace(
          "`", "\\`"
     ).replace(
          ">", "\\>"
     ).replace(
          "#", "\\#"
     ).replace(
          "+", "\\+"
     ).replace(
          "=", "\\="
     ).replace(
          "|", "\\|"
     ).replace(
          "{", "\\{"
     ).replace(
          "}", "\\}"
     ).replace(
          "!", "\\!"
     )

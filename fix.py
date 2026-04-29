import os
path = r'C:\Users\whent\AppData\Local\Programs\Python\Python310\lib\site-packages\pysip'
for fname in os.listdir(path):
    if fname.endswith('.py'):
        fpath = os.path.join(path, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content = content.replace('from PySIP.', 'from pysip.').replace('import PySIP.', 'import pysip.')
        if new_content != content:
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print('Fixed:', fname)
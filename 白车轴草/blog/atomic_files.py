import json
import os
import shutil
import tempfile


def atomic_write_json(file_path, value, *, sort_keys=False):
    directory_path = os.path.dirname(file_path) or '.'
    os.makedirs(directory_path, exist_ok=True)
    temporary_file_path = ''
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=directory_path,
            prefix='.clover-',
            suffix='.tmp',
            delete=False,
        ) as temporary_file:
            temporary_file_path = temporary_file.name
            json.dump(
                value,
                temporary_file,
                ensure_ascii=False,
                indent=2,
                sort_keys=sort_keys,
            )
            temporary_file.write('\n')
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_file_path, file_path)
    finally:
        if temporary_file_path and os.path.isfile(temporary_file_path):
            os.remove(temporary_file_path)


def atomic_copy_file(source_file_path, target_file_path):
    directory_path = os.path.dirname(target_file_path) or '.'
    os.makedirs(directory_path, exist_ok=True)
    temporary_file_path = ''
    try:
        with open(source_file_path, 'rb') as source_file:
            with tempfile.NamedTemporaryFile(
                mode='wb',
                dir=directory_path,
                prefix='.clover-',
                suffix='.tmp',
                delete=False,
            ) as temporary_file:
                temporary_file_path = temporary_file.name
                shutil.copyfileobj(source_file, temporary_file)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
        os.replace(temporary_file_path, target_file_path)
    finally:
        if temporary_file_path and os.path.isfile(temporary_file_path):
            os.remove(temporary_file_path)


def atomic_write_bytes(file_path, file_bytes):
    directory_path = os.path.dirname(file_path) or '.'
    os.makedirs(directory_path, exist_ok=True)
    temporary_file_path = ''
    try:
        with tempfile.NamedTemporaryFile(
            mode='wb',
            dir=directory_path,
            prefix='.clover-',
            suffix='.tmp',
            delete=False,
        ) as temporary_file:
            temporary_file_path = temporary_file.name
            temporary_file.write(file_bytes)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_file_path, file_path)
    finally:
        if temporary_file_path and os.path.isfile(temporary_file_path):
            os.remove(temporary_file_path)

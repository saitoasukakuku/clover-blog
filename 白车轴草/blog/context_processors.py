import hashlib
import os
import re
from urllib.parse import quote

from django.conf import settings
from django.db.models import Q

from blog.models import FriendRequest, Notification, PrivateMessage
from blog.site_owner import get_site_owner_profile


MUSIC_DIR_NAME = 'music'
MUSIC_CACHE_DIR_NAME = 'music_cache'
MUSIC_AUDIO_EXTENSIONS = {'.mp3', '.ogg', '.wav', '.m4a', '.flac'}
MUSIC_WEB_PLAYBACK_SUFFIX = '.web'
MUSIC_WEB_PLAYBACK_EXTENSIONS = ('.m4a', '.mp3', '.ogg')
MUSIC_WEB_PLAYBACK_FILE_SUFFIXES = tuple(
    f'{MUSIC_WEB_PLAYBACK_SUFFIX}{extension}'
    for extension in MUSIC_WEB_PLAYBACK_EXTENSIONS
)
MUSIC_COVER_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp')
MUSIC_LYRICS_EXTENSIONS = ('.lrc', '.txt')

_SITE_MUSIC_CACHE = {
    'signature': None,
    'tracks': [],
}


def footer_social(request):
    owner, profile = get_site_owner_profile()
    can_configure_social = (
        request.user.is_authenticated
        and owner is not None
        and request.user.pk == owner.pk
    )

    return {
        'footer_profile': profile,
        'footer_email': owner.email if owner else '',
        'footer_can_configure_social': can_configure_social,
    }


def social_counts(request):
    if not request.user.is_authenticated:
        return {
            'pending_friend_request_count': 0,
            'unread_private_message_count': 0,
            'unread_notification_count': 0,
        }

    return {
        'pending_friend_request_count': FriendRequest.objects.filter(
            receiver=request.user,
            status='pending',
        ).count(),
        'unread_private_message_count': PrivateMessage.objects.filter(
            recipient=request.user,
            is_read=False,
        ).filter(
            Q(sender__friendships_as_high__user_low=request.user)
            | Q(sender__friendships_as_low__user_high=request.user)
        ).distinct().count(),
        'unread_notification_count': Notification.objects.filter(
            recipient=request.user,
            is_read=False,
        ).count(),
    }


def build_media_file_url(directory_name, file_name):
    return f"{settings.MEDIA_URL.rstrip('/')}/{directory_name}/{quote(file_name)}"


def split_music_file_name(audio_file_name):
    file_stem, audio_extension = os.path.splitext(audio_file_name)
    is_web_playback_file = (
        audio_extension.lower() in MUSIC_WEB_PLAYBACK_EXTENSIONS
        and file_stem.casefold().endswith(MUSIC_WEB_PLAYBACK_SUFFIX)
    )
    if is_web_playback_file:
        return file_stem[:-len(MUSIC_WEB_PLAYBACK_SUFFIX)], audio_extension, True
    return file_stem, audio_extension, False


def decode_id3_syncsafe_size(size_bytes):
    size_value = 0
    for size_byte in size_bytes:
        size_value = (size_value << 7) | (size_byte & 0x7F)
    return size_value


def decode_id3_text(encoding_byte, text_bytes):
    encoding_names = {
        0: 'latin-1',
        1: 'utf-16',
        2: 'utf-16-be',
        3: 'utf-8',
    }
    encoding_name = encoding_names.get(encoding_byte, 'utf-8')
    return text_bytes.decode(encoding_name, errors='replace').strip('\x00').strip()


def split_id3_encoded_text(raw_bytes, encoding_byte):
    terminator = b'\x00\x00' if encoding_byte in {1, 2} else b'\x00'
    terminator_index = raw_bytes.find(terminator)
    if terminator_index == -1:
        return raw_bytes, b''
    return (
        raw_bytes[:terminator_index],
        raw_bytes[terminator_index + len(terminator):],
    )


def extract_id3_text_frame(frame_payload):
    if not frame_payload:
        return ''
    return decode_id3_text(frame_payload[0], frame_payload[1:])


def get_cover_extension_from_mime_type(mime_type):
    normalized_mime_type = mime_type.lower()
    if 'png' in normalized_mime_type:
        return '.png'
    if 'webp' in normalized_mime_type:
        return '.webp'
    return '.jpg'


def extract_id3_apic_frame(frame_payload):
    if len(frame_payload) < 4:
        return None

    encoding_byte = frame_payload[0]
    remaining_payload = frame_payload[1:]
    mime_type_end_index = remaining_payload.find(b'\x00')
    if mime_type_end_index == -1:
        return None

    mime_type = remaining_payload[:mime_type_end_index].decode('latin-1', errors='replace')
    remaining_payload = remaining_payload[mime_type_end_index + 1:]
    if not remaining_payload:
        return None

    remaining_payload = remaining_payload[1:]
    _, image_bytes = split_id3_encoded_text(remaining_payload, encoding_byte)
    if not image_bytes:
        return None

    return {
        'extension': get_cover_extension_from_mime_type(mime_type),
        'bytes': image_bytes,
    }


def extract_id3_uslt_frame(frame_payload):
    if len(frame_payload) < 5:
        return ''

    encoding_byte = frame_payload[0]
    remaining_payload = frame_payload[4:]
    _, lyrics_bytes = split_id3_encoded_text(remaining_payload, encoding_byte)
    return decode_id3_text(encoding_byte, lyrics_bytes)


def read_binary_uint32(raw_bytes, offset):
    if offset + 4 > len(raw_bytes):
        return None, offset
    return int.from_bytes(raw_bytes[offset:offset + 4], 'big'), offset + 4


def extract_flac_picture_block(block_payload):
    offset = 0
    _, offset = read_binary_uint32(block_payload, offset)

    mime_type_length, offset = read_binary_uint32(block_payload, offset)
    if mime_type_length is None or offset + mime_type_length > len(block_payload):
        return None
    mime_type = block_payload[offset:offset + mime_type_length].decode('ascii', errors='replace')
    offset += mime_type_length
    if mime_type == '-->':
        return None

    description_length, offset = read_binary_uint32(block_payload, offset)
    if description_length is None or offset + description_length > len(block_payload):
        return None
    offset += description_length

    for _ in range(4):
        _, offset = read_binary_uint32(block_payload, offset)

    image_data_length, offset = read_binary_uint32(block_payload, offset)
    if image_data_length is None or offset + image_data_length > len(block_payload):
        return None

    image_bytes = block_payload[offset:offset + image_data_length]
    if not image_bytes:
        return None

    return {
        'extension': get_cover_extension_from_mime_type(mime_type),
        'bytes': image_bytes,
    }


def read_mp3_id3_metadata(audio_file_path):
    metadata = {
        'title': '',
        'embedded_cover': None,
        'embedded_lyrics': '',
    }

    try:
        with open(audio_file_path, 'rb') as audio_file:
            header = audio_file.read(10)
            if len(header) != 10 or header[:3] != b'ID3':
                return metadata

            id3_major_version = header[3]
            tag_size = decode_id3_syncsafe_size(header[6:10])
            tag_payload = audio_file.read(tag_size)
    except OSError:
        return metadata

    frame_offset = 0
    while frame_offset + 10 <= len(tag_payload):
        frame_header = tag_payload[frame_offset:frame_offset + 10]
        frame_id = frame_header[:4].decode('latin-1', errors='replace')
        if not re.match(r'^[A-Z0-9]{4}$', frame_id):
            break

        if id3_major_version == 4:
            frame_size = decode_id3_syncsafe_size(frame_header[4:8])
        else:
            frame_size = int.from_bytes(frame_header[4:8], 'big')
        if frame_size <= 0:
            break

        frame_payload_start = frame_offset + 10
        frame_payload_end = frame_payload_start + frame_size
        if frame_payload_end > len(tag_payload):
            break
        frame_payload = tag_payload[frame_payload_start:frame_payload_end]

        if frame_id == 'TIT2' and not metadata['title']:
            metadata['title'] = extract_id3_text_frame(frame_payload)
        elif frame_id == 'APIC' and metadata['embedded_cover'] is None:
            metadata['embedded_cover'] = extract_id3_apic_frame(frame_payload)
        elif frame_id == 'USLT' and not metadata['embedded_lyrics']:
            metadata['embedded_lyrics'] = extract_id3_uslt_frame(frame_payload)

        frame_offset = frame_payload_end

    return metadata


def read_flac_metadata(audio_file_path):
    metadata = {
        'title': '',
        'embedded_cover': None,
        'embedded_lyrics': '',
    }

    try:
        with open(audio_file_path, 'rb') as audio_file:
            if audio_file.read(4) != b'fLaC':
                return metadata

            while True:
                block_header = audio_file.read(4)
                if len(block_header) != 4:
                    break

                is_last_block = bool(block_header[0] & 0x80)
                block_type = block_header[0] & 0x7F
                block_size = int.from_bytes(block_header[1:4], 'big')
                block_payload = audio_file.read(block_size)
                if len(block_payload) != block_size:
                    break

                if block_type == 6 and metadata['embedded_cover'] is None:
                    metadata['embedded_cover'] = extract_flac_picture_block(block_payload)

                if is_last_block:
                    break
    except OSError:
        return metadata

    return metadata


def read_audio_metadata(audio_file_path, audio_extension):
    if audio_extension.lower() == '.flac':
        return read_flac_metadata(audio_file_path)
    return read_mp3_id3_metadata(audio_file_path)


def find_same_name_file(directory_path, file_stem, extensions):
    try:
        directory_file_names = sorted(os.listdir(directory_path), key=str.lower)
    except OSError:
        return '', ''

    file_names_by_key = {}
    for directory_file_name in directory_file_names:
        directory_file_path = os.path.join(directory_path, directory_file_name)
        if os.path.isfile(directory_file_path):
            file_names_by_key[directory_file_name.casefold()] = directory_file_name

    for extension in extensions:
        candidate_key = f'{file_stem}{extension}'.casefold()
        candidate_file_name = file_names_by_key.get(candidate_key)
        if candidate_file_name:
            return candidate_file_name, os.path.join(directory_path, candidate_file_name)
    return '', ''


def parse_lrc_timestamp(timestamp_text):
    minutes_text, seconds_text, fraction_text = re.match(
        r'^(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?$',
        timestamp_text,
    ).groups()
    fraction_text = fraction_text or '0'
    fraction_seconds = int(fraction_text) / (10 ** len(fraction_text))
    return round((int(minutes_text) * 60) + int(seconds_text) + fraction_seconds, 3)


def parse_lyrics_lines(raw_lyrics):
    if not raw_lyrics:
        return []

    timed_lines = []
    plain_lines = []
    timestamp_pattern = re.compile(r'\[(\d{1,2}:\d{2}(?:[.:]\d{1,3})?)\]')
    for raw_line in raw_lyrics.splitlines():
        cleaned_line = raw_line.strip()
        if not cleaned_line:
            continue

        timestamp_matches = list(timestamp_pattern.finditer(cleaned_line))
        if timestamp_matches:
            lyric_text = cleaned_line[timestamp_matches[-1].end():].strip()
            if not lyric_text:
                continue
            for timestamp_match in timestamp_matches:
                timed_lines.append({
                    'time': parse_lrc_timestamp(timestamp_match.group(1)),
                    'text': lyric_text,
                })
        else:
            plain_lines.append({
                'time': None,
                'text': cleaned_line,
            })

    if timed_lines:
        return sorted(timed_lines, key=lambda lyric_line: lyric_line['time'])
    return plain_lines


def get_safe_music_cache_file_name(file_stem, digest, extension):
    safe_file_stem = re.sub(r'[^A-Za-z0-9._-]+', '-', file_stem).strip('-._')
    safe_file_stem = safe_file_stem[:56] or 'music-cover'
    return f'{safe_file_stem}-{digest}{extension}'


def write_embedded_cover_cache(audio_file_name, audio_file_path, embedded_cover):
    try:
        audio_file_stat = os.stat(audio_file_path)
    except OSError:
        return ''

    cover_digest = hashlib.sha256(
        audio_file_name.encode('utf-8')
        + str(audio_file_stat.st_mtime_ns).encode('ascii')
        + embedded_cover['bytes']
    ).hexdigest()[:16]
    file_stem, _ = os.path.splitext(audio_file_name)
    cache_file_name = get_safe_music_cache_file_name(
        file_stem,
        cover_digest,
        embedded_cover['extension'],
    )
    cache_directory = os.path.join(settings.MEDIA_ROOT, MUSIC_CACHE_DIR_NAME)
    cache_file_path = os.path.join(cache_directory, cache_file_name)
    try:
        os.makedirs(cache_directory, exist_ok=True)
        if not os.path.exists(cache_file_path):
            with open(cache_file_path, 'wb') as cache_file:
                cache_file.write(embedded_cover['bytes'])
    except OSError:
        return ''
    return build_media_file_url(MUSIC_CACHE_DIR_NAME, cache_file_name)


def read_text_file(file_path):
    for encoding_name in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            with open(file_path, 'r', encoding=encoding_name) as text_file:
                return text_file.read()
        except UnicodeDecodeError:
            continue
        except OSError:
            return ''
    return ''


def build_music_track(music_directory, audio_file_name):
    audio_file_path = os.path.join(music_directory, audio_file_name)
    file_stem, audio_extension, _ = split_music_file_name(audio_file_name)
    audio_metadata = read_audio_metadata(audio_file_path, audio_extension)
    playback_file_name, _ = find_same_name_file(
        music_directory,
        file_stem,
        MUSIC_WEB_PLAYBACK_FILE_SUFFIXES,
    )
    if not playback_file_name:
        playback_file_name = audio_file_name

    cover_file_name, _ = find_same_name_file(
        music_directory,
        file_stem,
        MUSIC_COVER_EXTENSIONS,
    )
    lyrics_file_name, lyrics_file_path = find_same_name_file(
        music_directory,
        file_stem,
        MUSIC_LYRICS_EXTENSIONS,
    )

    cover_url = ''
    if cover_file_name:
        cover_url = build_media_file_url(MUSIC_DIR_NAME, cover_file_name)
    elif audio_metadata['embedded_cover']:
        cover_url = write_embedded_cover_cache(
            audio_file_name,
            audio_file_path,
            audio_metadata['embedded_cover'],
        )

    raw_lyrics = ''
    if lyrics_file_name:
        raw_lyrics = read_text_file(lyrics_file_path)
    elif audio_metadata['embedded_lyrics']:
        raw_lyrics = audio_metadata['embedded_lyrics']

    return {
        'title': audio_metadata['title'] or file_stem,
        'audio_url': build_media_file_url(MUSIC_DIR_NAME, playback_file_name),
        'is_web_playback': playback_file_name != audio_file_name,
        'cover_url': cover_url,
        'lyrics_lines': parse_lyrics_lines(raw_lyrics),
    }


def build_music_directory_signature(music_directory):
    try:
        file_names = sorted(os.listdir(music_directory), key=str.lower)
    except OSError:
        return ()

    signature_items = []
    for file_name in file_names:
        file_path = os.path.join(music_directory, file_name)
        if not os.path.isfile(file_path):
            continue
        file_stat = os.stat(file_path)
        signature_items.append((file_name, file_stat.st_size, file_stat.st_mtime_ns))
    return tuple(signature_items)


def get_site_music_tracks():
    music_directory = os.path.join(settings.MEDIA_ROOT, MUSIC_DIR_NAME)
    signature = (
        settings.MEDIA_ROOT,
        settings.MEDIA_URL,
        build_music_directory_signature(music_directory),
    )
    if _SITE_MUSIC_CACHE['signature'] == signature:
        return _SITE_MUSIC_CACHE['tracks']

    tracks = []
    try:
        audio_file_names = sorted(os.listdir(music_directory), key=str.lower)
    except OSError:
        audio_file_names = []

    audio_file_stems = set()
    for audio_file_name in audio_file_names:
        audio_file_path = os.path.join(music_directory, audio_file_name)
        file_stem, audio_extension, is_web_playback_file = split_music_file_name(audio_file_name)
        if audio_extension.lower() not in MUSIC_AUDIO_EXTENSIONS:
            continue
        if not os.path.isfile(audio_file_path):
            continue
        if not is_web_playback_file:
            audio_file_stems.add(file_stem.casefold())

    for audio_file_name in audio_file_names:
        audio_file_path = os.path.join(music_directory, audio_file_name)
        file_stem, audio_extension, is_web_playback_file = split_music_file_name(audio_file_name)
        if audio_extension.lower() not in MUSIC_AUDIO_EXTENSIONS:
            continue
        if not os.path.isfile(audio_file_path):
            continue
        if is_web_playback_file and file_stem.casefold() in audio_file_stems:
            continue
        tracks.append(build_music_track(music_directory, audio_file_name))

    _SITE_MUSIC_CACHE['signature'] = signature
    _SITE_MUSIC_CACHE['tracks'] = tracks
    return tracks


def site_music_tracks(request):
    return {
        'site_music_tracks': get_site_music_tracks(),
    }

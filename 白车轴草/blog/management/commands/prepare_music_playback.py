import os
import shutil
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


MUSIC_DIRECTORY_NAME = 'music'
SOURCE_EXTENSIONS = {'.flac', '.wav', '.mp3', '.m4a', '.ogg'}
WEB_PLAYBACK_SUFFIX = '.web'


def is_web_playback_file(file_stem):
    return file_stem.casefold().endswith(WEB_PLAYBACK_SUFFIX)


def get_web_playback_file_name(source_file_name):
    file_stem, source_extension = os.path.splitext(source_file_name)
    if source_extension.lower() == '.mp3':
        return f'{file_stem}.web.mp3'
    return f'{file_stem}.web.m4a'


def should_prepare_web_playback(source_path, output_path, force):
    if force:
        return True
    if not os.path.exists(output_path):
        return True
    return os.path.getmtime(output_path) < os.path.getmtime(source_path)


def build_ffmpeg_command(ffmpeg_path, source_path, output_path, bitrate):
    command_arguments = [
        ffmpeg_path,
        '-y',
        '-i',
        source_path,
        '-vn',
    ]
    if output_path.lower().endswith('.web.mp3'):
        command_arguments.extend(['-c:a', 'libmp3lame', '-b:a', bitrate])
    else:
        command_arguments.extend(['-c:a', 'aac', '-b:a', bitrate, '-movflags', '+faststart'])
    command_arguments.append(output_path)
    return command_arguments


class Command(BaseCommand):
    help = 'Create browser-friendly web playback files for music tracks.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--bitrate',
            default='320k',
            help='Audio bitrate for generated playback files. Default: 320k.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Regenerate web playback files even when they are current.',
        )
        parser.add_argument(
            '--continue-on-error',
            action='store_true',
            help='Continue deployment when an individual audio file fails to transcode.',
        )

    def handle(self, *args, **options):
        music_directory = os.path.join(settings.MEDIA_ROOT, MUSIC_DIRECTORY_NAME)
        if not os.path.isdir(music_directory):
            self.stdout.write(f'Music directory does not exist; skipped: {music_directory}')
            return

        ffmpeg_path = shutil.which('ffmpeg')
        if not ffmpeg_path:
            self.stdout.write('ffmpeg is not installed; skipped music playback preparation.')
            return

        converted_count = 0
        skipped_count = 0
        failed_files = []

        for source_file_name in sorted(os.listdir(music_directory), key=str.lower):
            source_path = os.path.join(music_directory, source_file_name)
            if not os.path.isfile(source_path):
                continue

            file_stem, source_extension = os.path.splitext(source_file_name)
            if source_extension.lower() not in SOURCE_EXTENSIONS:
                continue
            if is_web_playback_file(file_stem):
                skipped_count += 1
                continue

            output_file_name = get_web_playback_file_name(source_file_name)
            output_path = os.path.join(music_directory, output_file_name)
            if not should_prepare_web_playback(source_path, output_path, options['force']):
                skipped_count += 1
                continue

            command_arguments = build_ffmpeg_command(
                ffmpeg_path,
                source_path,
                output_path,
                options['bitrate'],
            )
            try:
                subprocess.run(command_arguments, check=True)
                converted_count += 1
                self.stdout.write(f'Prepared web playback: {output_file_name}')
            except subprocess.CalledProcessError as error:
                failed_files.append(source_file_name)
                self.stderr.write(f'Failed to prepare {source_file_name}: {error}')
                if not options['continue_on_error']:
                    raise CommandError(f'Failed to prepare music playback: {source_file_name}') from error

        self.stdout.write(
            f'Music playback preparation complete: {converted_count} converted, '
            f'{skipped_count} skipped, {len(failed_files)} failed.'
        )

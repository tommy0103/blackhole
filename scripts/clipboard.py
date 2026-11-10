"""WSL clipboard capture and atomic, no-clobber publication inside the project."""
import base64
import os
import secrets
import subprocess
import sys


PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def output_parts(relative):
    parts = relative.split('/')
    if relative.startswith('/') or '..' in parts or not relative.endswith('.png'):
        raise ValueError('clipboard output must be a relative .png path inside the project')
    return [part for part in parts if part not in ('', '.')]


def save_image(relative, data):
    parts = output_parts(relative)
    # Pin each directory before opening its children. A concurrent rename or
    # symlink replacement cannot redirect later operations to the host filesystem.
    directory = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = None
    try:
        for component in parts[:-1]:
            try:
                os.mkdir(component, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child

        candidate = '.blackhole-clipboard.' + secrets.token_hex(16)
        descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        temporary = candidate
        with os.fdopen(descriptor, 'wb') as image:
            image.write(data)
            image.flush()
            os.fsync(image.fileno())
            # linkat publishes the complete file atomically and fails if ANY
            # destination entry exists, including a symlink to a directory.
            # Neither the source nor destination symlinks are followed.
            os.link(temporary, parts[-1], src_dir_fd=directory,
                    dst_dir_fd=directory, follow_symlinks=False)
            published = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            written = os.fstat(image.fileno())
            if (published.st_dev, published.st_ino) != (written.st_dev, written.st_ino):
                os.unlink(parts[-1], dir_fd=directory)
                raise ValueError('clipboard temporary file was replaced during save')
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)
    return '/workspace/' + '/'.join(parts)


def capture(script):
    windows_script = subprocess.check_output(['wslpath', '-w', script], text=True).strip()
    encoded = subprocess.check_output([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-File', windows_script,
    ])
    data = base64.b64decode(encoded.strip(), validate=True)
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError('clipboard did not produce a PNG image')
    return data


def main():
    try:
        relative, script = sys.argv[1:]
        output_parts(relative)
        print(save_image(relative, capture(script)))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('blackhole: error: could not save Windows clipboard image: ' + str(error),
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

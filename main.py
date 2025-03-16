#!/bin/python3

import subprocess
import os
import sys
import zoneinfo
import re

class Partitioning:
    BOOT_SIZE = 512  # MB
    SWAP_SIZE = 1024  # MB

    def __init__(self, device: str, root_partition_size: int = 0):
        self.device = device
        self.root_partition_size = root_partition_size
        self.total_disk_size = self.get_disk_size()
        self.remaining_size = self.total_disk_size - (self.BOOT_SIZE + self.SWAP_SIZE)
        if self.has_separate_home_partition:
            self.remaining_size -= self.root_partition_size

    @property
    def has_separate_home_partition(self) -> bool:
        return self.root_partition_size > 0

    def get_disk_size(self):
        result = subprocess.run(['lsblk', '-b', '-n', '-d', '-o', 'SIZE', self.device], capture_output=True, text=True, check=True)
        return int(result.stdout.strip()) // (1024 ** 2)

    def apply(self):
        if self.root_partition_size and self.root_partition_size >= self.remaining_size:
            raise ValueError('Root partition size is too large for the available space.')

        print(f'Creating partitions on {self.device}:')
        print(f'  Boot partition: {self.BOOT_SIZE}MB')
        print(f'  Swap partition: {self.SWAP_SIZE}MB')

        root_and_home_size = self.remaining_size if not self.has_separate_home_partition else self.root_partition_size
        print(f'  Root partition: {root_and_home_size}MB')

        if self.has_separate_home_partition:
            home_size = self.remaining_size - self.root_partition_size
            print(f'  Home partition: {home_size}MB')

        self.create_partitions(root_and_home_size, home_size if self.has_separate_home_partition else None)

    def create_partitions(self, root_size, home_size=None):
        subprocess.run(['parted', self.device, '--script', 'mklabel', 'gpt'], check=True)

        self.create_partition('fat32', 0, self.BOOT_SIZE)  # Boot partition
        self.create_partition('linux-swap', self.BOOT_SIZE, self.SWAP_SIZE)  # Swap partition
        self.create_partition('ext4', self.BOOT_SIZE + self.SWAP_SIZE, root_size)  # Root partition

        if home_size is not None:
            self.create_partition('ext4', self.BOOT_SIZE + self.SWAP_SIZE + root_size, home_size)  # Home partition

        self.format_partition(f'{self.device}p1', 'fat32')
        self.format_partition(f'{self.device}p2', 'swap')
        self.format_partition(f'{self.device}p3', 'ext4')
        if home_size:
            self.format_partition(f'{self.device}p4', 'ext4')

    def create_partition(self, fstype, start, size):
        subprocess.run(['parted', self.device, '--script', 'mkpart', 'primary', fstype, f'{start}MB', f'{start + size}MB'], check=True)

    def format_partition(self, partition, fstype):
        if fstype == 'fat32':
            subprocess.run(['mkfs.fat', '-F32', partition], check=True)
        elif fstype == 'swap':
            subprocess.run(['mkswap', partition], check=True)
        elif fstype == 'ext4':
            subprocess.run(['mkfs.ext4', partition], check=True)

    def mount(self):
        print('Mounting boot, swap, root (and home if it exists)')

        subprocess.run(['mount', f'{self.device}p3', '/mnt'], check=True)
        if self.has_separate_home_partition:
            subprocess.run(['mkdir', '-p', '/mnt/home'], check=True)
            subprocess.run(['mount', f'{self.device}p4', '/mnt/home'], check=True)
        subprocess.run(['mkdir', '-p', '/mnt/boot'], check=True)
        subprocess.run(['mount', f'{self.device}p1', '/mnt/boot'], check=True)
        subprocess.run(['swapon', f'{self.device}p2'], check=True)

    def serialize(self):
        return {'device': self.device, 'root_partition_size': self.root_partition_size}

    @staticmethod
    def deserialize(d: dict) -> 'Partitioning':
        return Partitioning(d['device'], d.get('root_partition_size', 0))

    def __repr__(self):
        rps = f', root_partition_size={self.root_partition_size}'
        return f'Partitioning(device={self.device!r}{rps if self.has_separate_home_partition else ""})'

class User:
    def __init__(self, username: str, password: str, sudo: bool):
        self.username = username
        self.password = password
        self.sudo = sudo
    
    def serialize(self):
        return {'username': self.username, 'password': self.password, 'sudo': self.sudo}
    
    @staticmethod
    def deserialize(d: dict) -> 'User':
        return User(d['username'], d['password'], d['sudo'])

    def __repr__(self):
        return f'User({self.username!r}, {self.password!r}, sudo={self.sudo})'

class General:
    def __init__(self, timezone: str, hostname: str, users: list[User]):
        self.timezone = timezone
        self.hostname = hostname
        self.users = users
    
    def serialize(self):
        return {
            'timezone': self.timezone,
            'hostname': self.hostname,
            'users': [user.serialize() for user in self.users]
        }

    @staticmethod
    def deserialize(d: dict) -> 'General':
        users = [User.deserialize(user) for user in d['users']]
        return General(d['timezone'], d['hostname'], users)

    def __repr__(self):
        users_list = users_list = ',\n'.join(f'    {user!r}' for user in self.users)
        return f'General(\n  timezone={self.timezone}\n  hostname={self.hostname},\n  users=[\n{users_list}\n  ]\n)'

class PostInstall:
    def __init__(self, packages: list[str], scripts: list[str]):
        self.packages = packages
        self.scripts = scripts
    
    def install_packages(self):
        subprocess.run(['arch-chroot', '/mnt', 'pacman', '-S', '--noconfirm'] + self.packages)

    def download_and_run_scripts(self):
        os.makedirs('/mnt/tmp', exist_ok=True)
        for script in self.scripts:
            script_name = script.split('/')[-1]
            try:
                subprocess.run(['curl', '-o', f'/mnt/tmp/{script_name}', script], check=True)
                subprocess.run(['chmod', '+x', f'/mnt/tmp/{script_name}'], check=True)
                subprocess.run(['arch-chroot', '/mnt', f'/tmp/{script_name}'], check=True)
            except:
                print('Error downloading/running script', script)
                pass

    def install(self):
        print('Post installation started')
        self.install_packages()
        self.download_and_run_scripts()
        print('Post installation completed')

    def serialize(self):
        return {
            'packages': self.packages,
            'scripts': self.scripts
        }

    @staticmethod
    def deserialize(d: dict) -> 'PostInstall':
        return PostInstall(d['packages'], d['scripts'])

    def __repr__(self):
        packages_list = '[\n' + ''.join(f'    {repr(pkg)},\n' for pkg in self.packages) + '  ]'
        scripts_list = '[\n' + ''.join(f'    {repr(script)},\n' for script in self.scripts) + '  ]'
        return f'PostInstall(\n  packages={packages_list},\n  scripts={scripts_list}\n)'

class Installer:
    BOOT_ENTRY_TITLE = 'Arch Linux'

    def __init__(self, general: General, partitioning: Partitioning, post_install: PostInstall):
        self.general = general
        self.partitioning = partitioning
        self.post_install = post_install
    
    def pacstrap(self):
        print('Running pacstrap')
        subprocess.run(['pacstrap', '/mnt', 'base', 'linux', 'linux-firmware', 'sudo'], check=True)

    def genfstab(self):
        print('Making fstab')
        with open('/mnt/etc/fstab', 'a') as f:
            subprocess.run(['genfstab', '-U', '/mnt'], stdout=f, check=True)

    def set_hostname(self):
        print('Setting hostname')
        with open('/mnt/etc/hostname', 'w') as f:
            f.write(self.general.hostname)

    def edit_sudoers(self):
        print('Editing sudoers')
        with open('/mnt/etc/sudoers', 'a') as f:
            f.write('\n%wheel ALL=(ALL:ALL) ALL\n')
    
    def make_hosts(self):
        print('Making hosts')
        with open('/mnt/etc/hosts', 'w') as f:
            f.write('127.0.0.1\tlocalhost\n')
            f.write('::1\tlocalhost\n')
            f.write(f'127.0.1.1\t{self.general.hostname}.localdomain\t{self.general.hostname}\n')

    def setup_time(self):
        print('Setting up time')
        path = f'/usr/share/zoneinfo/{self.general.timezone.replace("_", "/")}'
        subprocess.run(['ln', '-sf', path, '/mnt/etc/localtime'], check=True)
        subprocess.run(['arch-chroot', '/mnt', 'hwclock', '--systohc'], check=True)

    def locale_gen(self):
        print('Generating locale')
        with open('/mnt/etc/locale.gen', 'a') as f:
            f.write('en_US.UTF-8 UTF-8\n')
        subprocess.run(['arch-chroot', '/mnt', 'locale-gen'], check=True)
        with open('/mnt/etc/locale.conf', 'a') as f:
            f.write('\nLANG=en_US.UTF-8\n')

    def add_users(self):
        print('Adding users')
        for user in self.general.users:
            subprocess.run(['arch-chroot', '/mnt', 'useradd', '-m', '-G', 'wheel', user.username], check=True)
            subprocess.run(['arch-chroot', '/mnt', 'chpasswd'], input=f'{user.username}:{user.password}', text=True, check=True)

    def lock_root(self):
        print('Locking root account')
        subprocess.run(['arch-chroot', '/mnt', 'passwd', '-l', 'root'], check=True)

    def install_bootloader(self):
        print('Install bootloader')
        subprocess.run(['arch-chroot', '/mnt', 'bootctl', 'install'], check=True)
    
    def make_loader_config(self):
        print('Making loader config')
        with open('/mnt/boot/loader/loader.conf', 'w') as f:
            f.write('default linux\n')
            f.write('timeout 3\n')
            f.write('editor no\n')
    
    def make_boot_entry(self):
        print('Making boot entry')
        with open('/mnt/boot/loader/entries/linux.conf', 'w') as f:
            f.write(f'title {Installer.BOOT_ENTRY_TITLE}\n')
            f.write('linux /vmlinuz-linux\n')
            f.write('initrd /initramfs-linux.img\n')

            part_uuid = subprocess.run(['blkid', '-s', 'PARTUUID', '-o', 'value', f'{self.partitioning.device}p3'], capture_output=True, text=True, check=True).stdout.strip()
            f.write(f'options root=PARTUUID={part_uuid} rw\n')

    def setup_bootloader(self):
        print('Setting up bootloader')
        self.install_bootloader()
        self.make_loader_config()
        self.make_boot_entry()

    def setup_network(self):
        print('Installing and enabling network manager')
        subprocess.run(['arch-chroot', '/mnt', 'pacman', '-S', '--noconfirm', 'networkmanager'], check=True)
        subprocess.run(['arch-chroot', '/mnt', 'systemctl', 'enable', 'NetworkManager'], check=True)

    def install(self):
        print('Installation started')
        self.partitioning.apply()
        self.partitioning.mount()
        self.pacstrap()
        self.genfstab()
        self.set_hostname()
        self.add_users()
        self.edit_sudoers()
        self.lock_root()
        self.make_hosts()
        self.setup_time()
        self.locale_gen()
        self.setup_bootloader()
        self.setup_network()
        self.post_install.install()
        print('Installation complete!')

    def serialize(self):
        return {
            'general': self.general.serialize(),
            'partitioning': self.partitioning.serialize(),
            'post_install': self.post_install.serialize()
        }

    @staticmethod
    def deserialize(d: dict) -> 'Installer':
        general = General.deserialize(d['general'])
        partitioning = Partitioning.deserialize(d['partitioning'])
        post_install = PostInstall.deserialize(d['post_install'])
        return Installer(general, partitioning, post_install)

    def __repr__(self):
        general_repr = repr(self.general).replace('\n', '\n  ')
        partitioning_repr = repr(self.partitioning).replace('\n', '\n  ')
        post_install = repr(self.post_install).replace('\n', '\n  ')
        return f'Installer(\n  {general_repr},\n  {partitioning_repr},\n  {post_install}\n)'

class InstallerTextWizard:
    VALID_UNIX = staticmethod(lambda x: x.isalnum() and len(x) > 0)

    def clear(self):
        print('\033c', end='')

    def ask(self, prompt: str, check=None):
        while True:
            value = input(f'{prompt}: ')
            if check:
                result = check(value)
                if result is True or isinstance(result, str) or isinstance(result, int):
                    return value if result is True else result
                print('Invalid input')
            else:
                return value

    def ask_yn(self, prompt: str):
        return not input(prompt).strip().lower().startswith('n')

    def ask_int(self, prompt: str, min: int = None, max: int = None):
        while True:
            try:
                value = int(input(f'{prompt}: '))
                if (min is None or value >= min) and (max is None or value <= max):
                    return value
                print(f'Value must be between {min} and {max}')
            except ValueError:
                print('Invalid input')

    def ask_size(self, prompt: str) -> int:
        def size_filter(value: str):
            value = value.strip().upper()
            match = re.match(r'^(\d+(?:\.\d+)?)([MG]?)$', value)
            if not match:
                print('Please enter a number followed by optional M or G (e.g., 512M, 1G, or just 512)')
                return False

            num, unit = match.groups()
            num = float(num)
            return int(num * 1024) if unit == 'G' else int(num)

        return self.ask(prompt, size_filter)

    def choose(self, prompt: str, items: list[str]):
        while True:
            for i, x in enumerate(items, start=1):
                print(f'{i}) {x}')
            choice = input(f'{prompt} (1-{len(items)} or /search): ')
            if choice.startswith('/'):
                query = choice[1:].lower()
                matches = [item for item in items if query in item.lower()]
                if matches:
                    return self.choose(prompt, matches)
                else:
                    print('No matches found')
            else:
                try:
                    index = int(choice) - 1
                    if 0 <= index < len(items):
                        return items[index]
                    else:
                        print(f'Please choose a number between 1 and {len(items)}')
                except ValueError:
                    print('Invalid input, please enter a number or a search query')

    def section(self, text: str):
        self.clear()
        print('ARCH LINUX INSTALLER')
        print(f'[{text}]')
        print()

    def collect_user(self):
        username = self.ask('Username', InstallerTextWizard.VALID_UNIX)
        if not username:
            return None
        password = self.ask('Password', lambda x: bool(x.strip()))
        sudo = self.ask_yn(f'Can {username} use sudo? (Y/n) ')
        print()
        return User(username, password, sudo)

    def collect_users(self):
        self.section('Users')
        users = []
        while True:
            user = self.collect_user()
            if user is None:
                break
            users.append(user)
        return users

    def review_general(self, hostname, timezone, users):
        self.section('Review General')
        print('Hostname:', hostname)
        print('Timezone:', timezone)
        for user in users:
            role = "Superuser" if user.sudo else "User"
            print(f'{role} {user.username}')
        if self.ask_yn('\nIs this ok? (Y/n) '):
            return General(timezone, hostname, users)
        return self.collect_general()

    def collect_general(self):
        self.section('General')
        hostname = self.ask('Hostname', InstallerTextWizard.VALID_UNIX)

        self.section('Timezone')
        timezone = self.choose('Timezone', zoneinfo.available_timezones())

        users = self.collect_users()
        return self.review_general(hostname, timezone, users)

    def review_partitioning(self, device, root_partition_size):
        self.section('Review Partitioning')
        print('Device:', device)
        if root_partition_size:
            print('Separate /home partition: yes')
            print('Root partition size:', root_partition_size)
        else:
            print('Separate /home partition: no')
        if self.ask_yn('Is this ok? (Y/n) '):
            return Partitioning(device, root_partition_size)
        return self.collect_partitioning()

    def collect_partitioning(self):
        self.section('Partitioning')
        print('Available drives:')
        lsblk_output = subprocess.run(['lsblk', '-d', '-o', 'NAME,SIZE'], capture_output=True, text=True, check=True).stdout
        devices = list(filter(None, lsblk_output.strip().split('\n')[1:]))
        device = '/dev/' + self.choose('Device', devices).split()[0]

        if not self.ask_yn('Do you want a separate /home partition? (Y/n) '):
            return self.review_partitioning(device, 0)

        root_partition_size = self.ask_size('Size of the root partition (rest will be given to /home)')
        return self.review_partitioning(device, root_partition_size)

    def confirm_install(self, general, partitioning):
        self.section('Confirm')
        i = Installer(general, partitioning, PostInstall([], []))
        print(i)

        if not self.ask_yn('Do you want to install? (Y/n) '):
            sys.exit('Installation aborted')
        if not self.ask_yn('This will wipe everything. Are you sure? (Y/n) '):
            sys.exit('Installation aborted')
        
        return i

    def run(self):
        general = self.collect_general()
        partitioning = self.collect_partitioning()
        installer = self.confirm_install(general, partitioning)
        installer.install()

if __name__ == '__main__':
    InstallerTextWizard().run()

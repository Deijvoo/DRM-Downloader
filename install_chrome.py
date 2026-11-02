"""Register this checkout as a native messaging host for the current Windows user."""
import json
import os
from chrome_bridge import BASE, HOST, extension_id


def main():
    if os.name != 'nt':
        raise SystemExit('Run Setup-Chrome.cmd on the Windows PC that will download videos.')
    import winreg
    manifest = BASE / 'chrome-native-host.json'
    manifest.write_text(json.dumps({
        'name': HOST,
        'description': 'Voyo Downloader',
        'path': str(BASE / 'Native-Voyo.cmd'),
        'type': 'stdio',
        'allowed_origins': [f'chrome-extension://{extension_id()}/'],
    }, indent=2), encoding='utf-8')
    registry_path = rf'Software\Google\Chrome\NativeMessagingHosts\{HOST}'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(manifest))
    print('Local connection installed. Keep this folder in its current location.')
    print('In Chrome, open chrome://extensions and enable Developer mode.')
    print('Click Load unpacked and select this folder:')
    print(BASE / 'chrome-extension')
    print('Pin Voyo Downloader. Open a Voyo episode and click its extension button.')


if __name__ == '__main__':
    main()

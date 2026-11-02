# DRM-Downloader

## What does this script do?
This is a modified version of TPD-Keys that automatically downloads, decrypts, verifies, and merges the mp4 file after grabbing the keys. This version is designed for streams that only use one key for audio and video.

## Fixes in this fork

This fork fixes omitted-country crashes, explicit mode selection, the empty PID prompt, and subprocess error reporting. The `--verbose` flag is now respected. Progress messages no longer print decryption keys, proxy credentials, or full command arguments. External-program errors are still printed as received; remove sensitive data before sharing logs.

Tests cover CLI routing with mocked network, device, and media-tool calls, plus real local subprocess success and failure. They do not verify downloads, licence acceptance, or Voyo compatibility. Device credentials and service-specific configuration are still required. The original single-key and MP4/M4A assumptions remain.

### Local setup and checks

Use Python 3.11 (the version used for this fork's tests). From the repository folder on macOS/Linux:

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest -v
python drmdl.py --help
```

On Windows PowerShell, create the environment with `py -3.11 -m venv .venv`, then use `.venv\Scripts\python.exe` instead of `python` for the remaining commands. Activation is optional.

The Python requirements do not install the external tools listed below. Those tools must also be on PATH for actual downloads. No account, device file, or external media tool is needed to run the tests.

## PATH Requirements
1. [python](https://www.python.org/downloads/release/python-390/)
2. [ffmpeg](https://ffmpeg.org/download.html)
3. [mp4decrypt](https://www.bento4.com/downloads/)
4. [aria2](https://github.com/aria2/aria2)
5. [yt-dlp](https://github.com/yt-dlp/yt-dlp)

## Use your existing Chrome login (Windows)

The Chrome extension uses the Voyo tab you already have open. You do not need to sign in again in a separate browser window.

### Install once

1. Extract the latest repository ZIP into a folder you will keep. Python 3.11 with Tcl/Tk, the media tools below, and your existing `.wvd` file are still required.
2. Double-click **`Setup-Chrome.cmd`**. It installs the Python requirements and registers the local connection for your Windows account; administrator access is not required.
3. In your usual Chrome, open **`chrome://extensions`** and turn on **Developer mode**.
4. Click **Load unpacked** and select the **`chrome-extension`** folder inside the extracted repository.
5. Pin **Voyo Downloader** using Chrome's Extensions menu.

### Download an episode

1. Open the episode on **voyo.markiza.sk** in your usual Chrome profile.
2. Click the extension, then **Use this Voyo tab**. The tab reloads so the app can identify the episode.
3. Press **Play**. When the local download window opens, click **Download**. On first use, select your existing `.wvd` file and locate any missing media tools.
4. Keep Chrome and the download window open until the video is saved. Click **Open folder** to find it.

The extension sends the selected tab's manifest and matching playback authorisation to the local Python app through Chrome native messaging. It does not export cookies, copy your Chrome profile, or use a remote service. Capture stops after a match or ten minutes. Closing Chrome or disabling the extension while downloading can terminate the local app. Chrome will request permissions for the listed Voyo/media hosts and communication with native applications.

If the extension cannot connect, rerun `Setup-Chrome.cmd` from the folder you currently use. If you move the folder, rerun setup and reload the extension from its new location. To uninstall, remove the extension from Chrome and delete the Windows registry key `HKEY_CURRENT_USER\Software\Google\Chrome\NativeMessagingHosts\com.deijvoo.voyo`.

Only Voyo Markíza episode links are supported. The extension fetches the signed manifest without cookies; if that fails, it reports an error and you can try the separate-browser workflow below. Automated checks cover the bridge protocol and simulated browser events. Live Voyo playback and a full Windows Chrome download still need verification.

## Separate-browser Voyo window (Windows)

Double-click `Start-Voyo.cmd`. The launcher needs Python 3.11 with Tcl/Tk and Google Chrome; it creates a Python environment and installs the GUI requirements on first use.

During one-time setup, choose your existing `.wvd` device file and download folder. The external tools listed above, including `ffprobe` from FFmpeg, must already be installed. If a tool cannot be found, the app asks you to locate its executable. Settings are remembered when a download starts.

For each episode:

1. Paste an HTTPS episode link from `voyo.markiza.sk` and click **Download**.
2. Sign in and play the episode in the Chrome window that opens.
3. Once saved, click **Open folder** to find the video.

Videos go to `Downloads/Voyo` by default. Repeat downloads receive numbered filenames; existing files are never replaced. **Settings** lets you change the device file and folder. Browser sign-in stays in a separate local profile.

This window supports Voyo Markíza episode links only. Netflix and other platforms are not supported. Automated tests cover local parsing, filenames, cancellation, and the mocked download pipeline; they do not verify live Voyo downloads or the Windows interface.

## Python Requirements
1. requests
2. frida
3. frida-tools
4. protobuf
5. pycryptodome
6. pywidevine
7. httpx

## How to use this program (Google Chrome)
### Preparation
1. Acquire a CDM file.wvd file (see below for guide to obtain CDM using an Android Studio emulator)
2. Install the [Tampermonkey](https://www.tampermonkey.net/) browser extension
3. Install the [EME Logger script](https://greasyfork.org/en/scripts/373903-eme-logger) in Tampermonkey
4. Open the Developer Tools with CTRL+SHIFT+I
5. In the Console tab of the developer window add this to the filter box: MediaKeySession::generateRequest
   - This makes the PSSH pop up in the Console tab when the video loads. 
6. In the Network tab of the developer window add this to the filter box: index.mpd
   - This makes the index.mpd pop up in the Network tab when the video loads. Could also be manifest.mpd
7. (Mode 7 only) In the Network tab of the developer window add this to the filter box: method:POST
   - Find the second ?specConform=true that pops up, click it, and scroll down in the request headers and 
   - find `X-Dt-Custom-Data` and copy the value as your -xdtcd value


### Running the script
1. You can just run the script with no arguments, and you will be prompted for the required inputs.
2. You can also run the script without the input prompt by passing arguments
 `drmdl.py -out "OUTPUT FILE PATH" -pssh "PSSH_HERE" -url "index.mpd URL HERE" -xdtcd "X-Dt-Custom-Data value here"`
### Options for all modes
* -out "path/to/outputfile.mp4"
* -wvd "PATH to WVD file"
* - Optional. Defaults to "./file.wvd"
* -url "URL for index.mpd"
* -pssh "PSSH value"
### Optional options for all modes
* -cc "COUNTRYCODE"
* - Set the two letter country code for the desired proxy country.
### Mode-specific options for each mode
* Use the -selection option to select a mode. The default mode is 7.
#### Mode 1 (Generic without headers)
* -lic "License server url here"
#### Mode 2 (Generic with generic headers)
* -lic "License server url here"
#### Mode 3 (Generic with custom headers from DRMHeaders.py)
* Set your desired custom headers in DRMHeaders.py
#### Mode 4 (JSON Widevine challenge, headers from DRMHeaders.py)
* Set your desired custom headers in DRMHeaders.py
* -lic "License server url here"
* -pid "PID here"
#### Mode 5 (Canal+ Live TV)
* -channel "EpgId here"
* -live_token "Live token here"
#### Mode 6 (YouTube)
* -lic "License server url here"
#### Mode 7 (Generic Header with X-Dt-Custom-Data)
* -xdtcd "Custom X-Dt-Custom-Data value here"
* -lic "License server url here"
    - Optional. Default value can be changed in the script. Defaults to https://lic.drmtoday.com/license-proxy-widevine/cenc/?specConform=true



## Common issues
###### FileNotFoundError on start_process
Make sure you have the dependencies installed and in your PATH

## How to get a CDM file
### Creating a wvd (widevine device file) by extracting keys from an android device.
Here I extract it from android studio emulator using this guide
https://forum.videohelp.com/threads/408031-Dumping-Your-own-L3-CDM-with-Android-Studio/

#### Prerequisites
1. First make sure to check if your PC supports Intel HAXM. If not you may need to use an android phone
2. Enable VT-x and VT-d in BIOS
3. Disable hypervisor on windows

#### Setup the emulator
1. Install android studio
2. Install intel HAXM when android studio asks
3. Create a device 
4. Pixel 6
5. Pie 28 / android 9.0
6. Run the new android device

#### Install python dependencies for everything
```
pip install frida
pip install frida-tools
pip install protobuf
pip install pycryptodome
pip install requests
pip install httpx
pip install pywidevine
```


#### Starting frida server on the emulator
##### Preparation
1. Download frida-server-x.x.x-android-x86.xz https://github.com/frida/frida/releases
2. Make sure it is the same version as the one you installed from pip
3. Extract it (I used peazip) and rename the file to "frida"
4. Move the file you renamed to frida to `C:\Users\yourname\AppData\Local\Android\Sdk\platform-tools`

##### Commands
1. Open CMD and navigate to the same directory
```cd C:\Users\yourname\AppData\Local\Android\Sdk\platform-tools```
2. Check if the android VM is running by running
```adb devices```
3. Now execute these commands to start the frida server on the android device
```
adb push frida /sdcard/
adb shell
su
cp /sdcard/frida /data/local/tmp
chmod +x /data/local/tmp/frida
/data/local/tmp/frida
```

#### Run wvdumper
https://github.com/wvdumper/dumper

```python dump_keys.py```

#### Dump the key files
1. Open Chrome on the android VM and go to any DRM protected video

Your CDM key files will be generated in the key_dumps folder:

`client_id.bin` and `private_key.pem`

The guide recommends renaming them to
`device_client_id_blob` and `device_private_key`


#### Create the device.wvd from the key files:
1. Make a new folder
2. Inside it add the device_client_id_blob and device_private_key files
3. And create a new folder called output
4. Then run this command to create the wvd file in the output folder
```
pywidevine create-device -k device_private_key -c device_client_id_blob -t "ANDROID" -l 3 -o output
```

Rename the file in the output folder to file.wvd and move it to the same folder as drmdl.py
Or rename to whatever you like and add `-wvd "pathtofile.wvd"` to the command

```
python drmdl.py -out "file.mp4" -pssh "" -url "" -xdtcd "" -wvd "pathtofile.wvd"
```



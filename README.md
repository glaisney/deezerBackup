# deezerBackup
Download your Deezer collections as plain TSV and JSON files

# Usage
1) get your Deezer id
2) Run the script, with one of these syntaxes :
   
  python deezer_playlist_export.py 12345678
  python deezer_playlist_export.py https://www.deezer.com/profile/12345678
  python deezer_playlist_export.py --playlist https://www.deezer.com/playlist/3155776842
  python deezer_playlist_export.py 12345678 --playlist 3155776842

No third-party dependencies, login, or developer API key required. Does not retrieve private playlists, hidden liked tracks, nor DRM-protected audio.

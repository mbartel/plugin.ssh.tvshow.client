import paramiko
import simplejson
import os.path
import urllib
import sys
import urlparse
import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import re

base_url = sys.argv[0]
addon_handle = int(sys.argv[1])
args = urlparse.parse_qs(sys.argv[2][1:])

xbmcplugin.setContent(addon_handle, 'tvshows')
xbmcplugin.addSortMethod(handle = addon_handle, sortMethod = xbmcplugin.SORT_METHOD_NONE)

addon = xbmcaddon.Addon('plugin.ssh.tvshow.client')
path = xbmc.translatePath(addon.getAddonInfo('path'))

def jsonrpc(method, resultKey, params):
  query = xbmc.executeJSONRPC('{"jsonrpc": "2.0", "method": "' + method + '", "params": ' + params + ', "id": 1}')
  result = simplejson.loads(unicode(query, 'utf-8', errors='ignore'))
  if result.has_key('result') and result['result'] != None and result['result'].has_key(resultKey):
    return result['result'][resultKey]
  else:
    return []


def get_tv_show_list_from_db():
  tvshows = jsonrpc('VideoLibrary.GetTVShows', 'tvshows', '{ "properties": ["title", "thumbnail"] }, "id": "libTvShows"}')
  tvshowList = dict()
  for tvshow in tvshows:
    episodes = jsonrpc(
      'VideoLibrary.GetEpisodes',
      'episodes',
      '{"tvshowid": %d, "properties": ["title", "season", "episode"]}' % tvshow['tvshowid']
    )

    lastSeasonNr = 0
    lastEpisodeNr = 0
    for episode in episodes:
      if (episode['season'] > lastSeasonNr):
        lastSeasonNr = episode['season']
        lastEpisodeNr = episode['episode']
        lastEpisode = episode
      elif (episode['season'] == lastSeasonNr and episode['episode'] > lastEpisodeNr):
        lastEpisodeNr = episode['episode']
        lastEpisode = episode

    if lastEpisode != None:
      tvshowList[tvshow['title']] = {
        'title': tvshow['title'],
        'season': int(lastEpisode['season']),
        'thumbnail': tvshow['thumbnail'],
        'episode': int(lastEpisode['episode']),
        'episodeTitle': lastEpisode['title'],
        'episodeDBId': lastEpisode['episodeid']
      }

  return tvshowList

def open_sftp_connection():
  host = addon.getSetting('host')
  port = int(addon.getSetting('port'))
  username = addon.getSetting('username')
  password = addon.getSetting('password')

  transport = paramiko.Transport((host, port))
  transport.start_client()
  transport.auth_password(username=username, password=password)

  if not transport.is_authenticated():
    xbmcgui.Dialog().ok(addon.getLocalizedString(10000), line1=addon.getLocalizedString(10001))

  sftp = paramiko.SFTPClient.from_transport(transport)

  return sftp, transport

def get_tv_show_list_from_remote_server():
  sftp, transport = open_sftp_connection()
  remoteFolder = addon.getSetting('remoteFolder')
  dirlist = sftp.listdir(remoteFolder)

  progressControl = xbmcgui.DialogProgress()
  progressControl.create(addon.getLocalizedString(10010), addon.getLocalizedString(10011))
  fetchingLabel = addon.getLocalizedString(10012)
  progressFactor = 100 / len(dirlist)
  progressCounter = 0

  tvshowDict = dict()
  seasonMatcher = re.compile('S.*?([0-9]+)$')
  episodeMatcher = re.compile('.*?[Se][0-9]+[Ee]([0-9]+)')

  for dir in dirlist:
    folderNames = sftp.listdir(remoteFolder + '/' + dir)
    lastSeason = 0
    lastSeasonFolder = ''
    for folderName in folderNames:
      foundMatches = seasonMatcher.match(folderName)
      if foundMatches is not None:
        season = foundMatches.group(1)
        if lastSeason < season:
          lastSeason = season
          lastSeasonFolder = folderName

    try:
      fileNames = sftp.listdir(remoteFolder.encode('utf-8', 'ignore') + '/' + dir.encode('utf-8', 'ignore') + '/' + lastSeasonFolder.encode('utf-8', 'ignore'))
    except UnicodeDecodeError, err:
      print 'ERROR:', err 
    
    lastEpisode = 0
    lastEpisodeFile = ''
    for fileName in fileNames:
      foundMatches = episodeMatcher.match(fileName)
      if foundMatches is not None:
        episode = foundMatches.group(1)
        if lastEpisode < episode:
          lastEpisode = episode
          lastEpisodeFile = fileName

    tvshowDict[dir] = {
      'title': dir,
      'season': int(lastSeason),
      'episode': int(lastEpisode),
      'episodeTitle': lastEpisodeFile.partition(u"E%s -"%lastEpisode)[2][:-4],
      'thumbnail': path + '/resources/media/download.png',
      'file': (u"%s/%s/%s/%s" % (remoteFolder, dir, lastSeasonFolder, lastEpisodeFile)).encode('utf-8')
    }

    progressCounter += 1
    progressControl.update(progressCounter * progressFactor, fetchingLabel, dir)
    if progressControl.iscanceled():
        break

  transport.close()

  return tvshowDict

def get_tv_show_season_list_from_remote_server(tvShow):
  seasonMatcher = re.compile('S.*?([0-9]+)$')
  remoteFolder = addon.getSetting('remoteFolder') + '/' + tvShow['title'][0]
  thumbnail = unicode(tvShow['thumbnail'][0]).encode('utf-8')
  thumbnailPath = xbmc.translatePath(thumbnail)

  if 'localLastSeason' in tvShow:
    season = tvShow['localLastSeason'][0]
    episode = tvShow['localLastEpisode'][0]
  else:
    season = 0
    episode = 0

  sftp, transport = open_sftp_connection()
  folderNames = sftp.listdir(remoteFolder)
  for folderName in sorted(folderNames):
    foundMatches = seasonMatcher.match(folderName)
    if foundMatches is not None and foundMatches.group(1) >= season:
      url = get_tvshow_url({'remotePath': remoteFolder + '/' + folderName, 'thumbnail': thumbnail, 'episode': episode }, 'showEpisodeList')
      li = xbmcgui.ListItem(folderName, thumbnailImage=thumbnailPath)
      xbmcplugin.addDirectoryItem(handle=addon_handle, url=url, listitem=li, isFolder=True)
  xbmcplugin.endOfDirectory(addon_handle)

  transport.close()

def get_tv_show_episode_list_from_remote_server(tvShow):
  episodeMatcher = re.compile('.*?[Se][0-9]+[Ee]([0-9]+)')
  thumbnail = xbmc.translatePath(unicode(tvShow['thumbnail'][0]).decode('utf-8'))
  episode = tvShow['episode'][0]
  remotePath = tvShow['remotePath'][0] + '/'

  sftp, transport = open_sftp_connection()
  fileNames = sftp.listdir(remotePath)
  for filename in sorted(fileNames):
    foundMatches = episodeMatcher.match(filename)
    if foundMatches is not None and foundMatches.group(1) >= episode:
      li = xbmcgui.ListItem(filename, thumbnailImage=thumbnail)
      xbmcplugin.addDirectoryItem(handle=addon_handle, url=get_tvshow_url({'fileName': remotePath + filename }, 'downloadFile'), listitem=li, isFolder=False)
  xbmcplugin.endOfDirectory(addon_handle)

  transport.close()

def get_compared_tv_show_list(localShows, remoteShows):
    for (remoteShowTitle, remoteShow) in remoteShows.iteritems():
      if (localShows.has_key(remoteShowTitle)):
        localShow = localShows.get(remoteShowTitle)
        remoteShow['thumbnail'] = localShow['thumbnail']
        remoteShow['localLastSeason'] = localShow['season']
        remoteShow['localLastEpisode'] = localShow['episode']
        remoteShow['hasNewEpisodes'] = bool(localShow['season'] <= remoteShow['season'] and localShow['episode'] < remoteShow['episode'])
      else:
        remoteShow['hasNewEpisodes'] = True

    return remoteShows;

def get_tvshow_url(tvshow, action):
  tvshowEncoded = dict()
  tvshowEncoded['action'] = action
  for k, v in tvshow.iteritems():
    if isinstance(v, unicode):
        v = v.encode('utf8')
    elif isinstance(v, str):
        v.decode('utf8')
    tvshowEncoded[k] = v
  return base_url + '?' + urllib.urlencode(tvshowEncoded)

def display_episode_list(tvShowList):
  for tvShow in tvShowList:
    if tvShow['hasNewEpisodes']:
      if 'localLastSeason' in tvShow:
        label = u"%s - [COLOR green][B]S%.2dE%.2d -> S%.2dE%.2d[/B][/COLOR] %s" % (tvShow['title'], tvShow['localLastSeason'], tvShow['localLastEpisode'], tvShow['season'], tvShow['episode'], tvShow['episodeTitle'])
      else:
        label = u"%s - [COLOR white][B]S%.2dE%.2d[/B][/COLOR] %s" % (tvShow['title'], tvShow['season'], tvShow['episode'], tvShow['episodeTitle'])
      li = xbmcgui.ListItem(label, thumbnailImage=xbmc.translatePath(unicode(tvShow['thumbnail']).encode('utf-8')))
      xbmcplugin.addDirectoryItem(handle=addon_handle, url=get_tvshow_url(tvShow, 'showSeasonList'), listitem=li, isFolder=True)
  xbmcplugin.endOfDirectory(addon_handle)

def update_file_transfer_progress(progressControl, sftp, localFile, transferred, toBeTransferred):
  percent = int(100.0 / toBeTransferred * transferred)
  progressControl.update(percent, addon.getLocalizedString(10020), localFile, "%d %% completed (%d / %d bytes)"%(percent, transferred, toBeTransferred))
  if progressControl.iscanceled():
    sftp.close()

def download_file(remoteFile):
  remoteFolder = addon.getSetting('remoteFolder').encode("utf-8")
  localFolder = addon.getSetting('localFolder').encode("utf-8")
  localPath = localFolder + os.path.dirname(remoteFile.replace(remoteFolder + '/', ''))
  localFile = remoteFile.replace(os.path.dirname(remoteFile) + '/', '')

  if (not os.path.exists(localPath)):
    os.makedirs(localPath)

  progressControl = xbmcgui.DialogProgress()
  progressControl.create(addon.getLocalizedString(10010), addon.getLocalizedString(10020))
  sftp, transport = open_sftp_connection()
  sftp.get(remoteFile, localPath + '/' + localFile, callback=lambda transferred, toBeTransferred: update_file_transfer_progress(progressControl, sftp, localFile, transferred, toBeTransferred))
  transport.close()

if args and 'action' in args:
  action = args['action'][0]
  if action == 'showSeasonList':
    get_tv_show_season_list_from_remote_server(args)
  elif action == 'showEpisodeList':
    get_tv_show_episode_list_from_remote_server(args)
  elif action == 'downloadFile':
    download_file(args['fileName'][0])
else:
  localTVShows = get_tv_show_list_from_db()
  remoteTVShows = get_tv_show_list_from_remote_server()
  comparedTYShows = get_compared_tv_show_list(localTVShows, remoteTVShows).values()
  display_episode_list(sorted(comparedTYShows, key=lambda x: x['title']))


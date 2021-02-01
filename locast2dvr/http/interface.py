import logging
import re
import subprocess
import threading
import pytz
from datetime import datetime, timedelta

from flask import Flask, Response, jsonify, redirect, request
from flask.templating import render_template
from locast2dvr.locast import LocastService
from locast2dvr.utils import Configuration


def HTTPInterface(config: Configuration, port: int, uid: str, locast_service: LocastService, station_scan=False) -> Flask:
    """Create a Flask app that is used to interface with PMS and acts like a DVR device

    Args:
        config (utils.Configuration): locast2dvr configuration object
        port (int): TCP port this app will be bound to
        uid (str): Unique ID for this app. PMS uses this to identify DVRs
        locast_service (locast.Service): Locast service object
        station_scan (bool): used for testing only (default: False)

    Returns:
        Flask: A Flask app that can interface with PMS and mimics a DVR device
    """
    log = logging.getLogger("HTTPInterface")
    app = Flask(__name__)

    host_and_port = f'{config.bind_address}:{port}'

    @app.route('/', methods=['GET'])
    @app.route('/device.xml', methods=['GET'])
    def device_xml() -> Response:
        """Render an XML when /device.xml is called.

        Returns:
            Response: XML response
        """
        xml = render_template('device.xml',
                              device_model=config.device_model,
                              device_version=config.device_version,
                              friendly_name=locast_service.city,
                              uid=uid,
                              host_and_port=host_and_port)
        return Response(xml, mimetype='text/xml')

    @app.route('/discover.json', methods=['GET'])
    def discover_json() -> Response:
        """Return data about the device in JSON

        Returns:
            Response: JSON response containing device information
        """
        data = {
            "FriendlyName": locast_service.city,
            "Manufacturer": "locast2dvr",
            "ModelNumber": config.device_model,
            "FirmwareName": config.device_firmware,
            "TunerCount": config.tuner_count,
            "FirmwareVersion": config.device_version,
            "DeviceID": uid,
            "DeviceAuth": "locast2dvr",
            "BaseURL": f"http://{host_and_port}",
            "LineupURL": f"http://{host_and_port}/lineup.json"
        }
        return jsonify(data)

    @app.route('/lineup_status.json', methods=['GET'])
    def lineup_status_json() -> Response:
        """Provide a (somewhat fake) status about the scanning process

        Returns:
            Response: JSON containing scanning information
        """
        if station_scan:
            lineup_status = {
                "ScanInProgress": True,
                "Progress": 50,
                "Found": 5
            }
        else:
            lineup_status = {
                "ScanInProgress": False,
                "ScanPossible": True,
                "Source": "Antenna",
                "SourceList": ["Antenna"]
            }
        return jsonify(lineup_status)

    @app.route('/lineup.m3u', methods=['GET'])
    @app.route('/tuner.m3u', methods=['GET'])
    def m3u() -> Response:
        """Returns all stations in m3u format

        Returns:
            Response: m3u in text/plain
        """
        m3uText = "#EXTM3U\n"
        for station in locast_service.get_stations():
            callsign = name_only(station.get("callSign_remapped") or station.get(
                "callSign") or station.get("name"))
            city = station["city"]
            logo = station.get("logoUrl") or station.get("logo226Url")
            channel = station.get("channel_remapped") or station["channel"]
            networks = "Network" if callsign in [
                'ABC', 'CBS', 'NBC', 'FOX', 'CW', 'PBS'] else ""
            groups = ";".join(filter(None, [city, networks]))
            url = f"http://{host_and_port}/watch/{station['id']}.m3u"

            tvg_name = f"{callsign} ({city})" if config.multiplex else callsign

            m3uText += f'#EXTINF:-1 tvg-id="channel.{station["id"]}" tvg-name="{tvg_name}" tvg-logo="{logo}" tvg-chno="{channel}" group-title="{groups}", {callsign}'

            if config.multiplex:
                m3uText += f' ({city})'
            m3uText += f'\n{url}\n\n'
        return m3uText

    @app.template_filter()
    def name_only(value: str) -> str:
        """Get the name part of a callSign. '4.1 CBS' -> 'CBS'

        Args:
            value (str): String to parse

        Returns:
            str: Parsed string or original value
        """
        m = re.match(r'\d+\.\d+ (.+)', value)
        if m:
            return m.group(1)
        else:
            return value

    @app.route('/lineup.json', methods=['GET'])
    def lineup_json() -> Response:
        """Returns a URL for each station that PMS can use to stream in JSON

        Returns:
            Response: JSON containing the GuideNumber, GuideName and URL for each channel
        """
        return jsonify([{
            "GuideNumber": station.get('channel_remapped') or station['channel'],
            "GuideName": station['name'],
            "URL": f"http://{host_and_port}/watch/{station['id']}"
        } for station in locast_service.get_stations()])

    @app.route('/epg', methods=['GET'])
    def epg() -> Response:
        """Returns the Electronic Programming Guide in json format

        Returns:
            Response: JSON containing the EPG for this DMA
        """
        return jsonify(locast_service.get_stations())

    @app.route('/config', methods=['GET'])
    def output_config() -> Response:
        """Returns the Electronic Programming Guide in json format

        Returns:
            Response: JSON containing the EPG for this DMA
        """
        c = dict(config)
        c['password'] = "*********"
        print(config)
        return jsonify(c)

    @app.template_filter()
    def format_date(value: int) -> str:
        """Convert an epoch timestamp to YYYYmmdd

        Args:
            value (str): Epoch timestamp string

        Returns:
            str: String as YYYYmmdd
        """

        return (datetime(1970, 1, 1) + timedelta(milliseconds=value)).strftime('%Y%m%d')

    @app.template_filter()
    def format_date_iso(value: int) -> str:
        """Convert an epoch timestamp to YYYY-mm-dd

        Args:
            value (str): Epoch timestamp string

        Returns:
            str: String as YYYY-mm-dd
        """

        return (datetime(1970, 1, 1) + timedelta(milliseconds=value)).strftime('%Y-%m-%d')

    @app.template_filter()
    def format_time(value: int) -> str:
        """Return an epoch timestamp to YYYYmmdddHHMMSS

        Args:
            value (str): Epoch timestamp string

        Returns:
            str: String as YYYYmmdddHHMMSS
        """
        return (datetime(1970, 1, 1) + timedelta(milliseconds=value)).strftime('%Y%m%d%H%M%S')

    @app.template_filter()
    def format_time_local_iso(value: int, timezone: str) -> str:
        """Return an epoch timestamp to YYYY-mm-dd HH:MM:SS in local timezone

        Args:
            value (int): Epoch timestamp string
            timezone (str): Time zone (e.g. America/New_York)

        Returns:
            str: String as YYYY-mm-dd HH:MM:SS
        """
        datetime_in_utc = datetime(1970, 1, 1) + timedelta(milliseconds=value)
        datetime_in_local = pytz.timezone(timezone).fromutc(datetime_in_utc)
        return datetime_in_local.strftime('%Y-%m-%d %H:%M:%S')

    @app.template_filter()
    def aspect(value: str) -> str:
        """Convert a locast 'videoProperties' string to an aspect ratio

        Args:
            value (str): locast 'videoProperties' string

        Returns:
            str: aspect ratio. Either '4:3' or '16:9'
        """
        for r in ["1080", "720", "HDTV"]:
            if r in value:
                return "16:9"
        return "4:3"

    @app.template_filter()
    def quality(value: str) -> str:
        """Convert a locast 'videoProperties' string to a quality

        Args:
            value (str): locast 'videoProperties' string

        Returns:
            str: quality. Either 'SD' or 'HDTV'
        """
        if "HDTV" in value:
            return "HDTV"
        else:
            return "SD"

    @app.route('/epg.xml', methods=['GET'])
    def epg_xml() -> Response:
        """Render the EPG as XMLTV. This will trigger a refetch of all stations from locast.

        Returns:
            Response: XMLTV
        """
        xml = render_template('epg.xml',
                              stations=locast_service.get_stations(),
                              url_base=host_and_port)
        return Response(xml, mimetype='text/xml')

    @app.route('/lineup.xml', methods=['GET'])
    def lineup_xml() -> Response:
        """Returns a URL for each station that PMS can use to stream in XML

        Returns:
            Response: XML containing the GuideNumber, GuideName and URL for each channel
        """
        xml = render_template('lineup.xml',
                              stations=locast_service.get_stations(),
                              url_base=host_and_port).encode("utf-8")
        return Response(xml, mimetype='text/xml')

    @app.route('/lineup.post', methods=['POST', 'GET'])
    def lineup_post():
        """Initiate a rescan of stations for this DVR"""
        scan = request.args.get('scan')
        if scan == 'start':
            station_scan = True
            stations = locast_service.get_stations()
            station_scan = False
            return ('', 204)

        return (f'{scan} is not a valid scan command', 400)

    @app.route('/watch/<channel_id>.m3u')
    def watch_m3u(channel_id: str) -> Response:
        """Stream the channel based on it's ID. This route redirects to a locast m3u.

        Args:
            channel_id (str): Channel ID

        Returns:
            Response: Redirect to a locast m3u
        """
        log.info(
            f"Watching channel {channel_id} on {host_and_port} for {locast_service.city} using m3u")
        return redirect(locast_service.get_station_stream_uri(channel_id), code=302)

    @app.route('/watch/<channel_id>')
    def watch(channel_id: str) -> Response:
        """Stream a channel based on it's ID. The route streams data as long as its connected.
           This method starts ffmpeg and reads n bytes at a time.

        Args:
            channel_id (str): Channel ID

        Returns:
            Response: HTTP response with content_type 'video/mpeg; codecs="avc1.4D401E"'
        """
        log.info(
            f"Watching channel {channel_id} on {host_and_port} for {locast_service.city} using ffmpeg")
        uri = locast_service.get_station_stream_uri(channel_id)

        ffmpeg = config.ffmpeg or 'ffmpeg'

        # Start ffmpeg as a subprocess to extract the mpeg stream and copy it to the incoming
        # connection. ffmpeg will take care of demuxing the mpegts stream and following m3u directions
        ffmpeg_cmd = [ffmpeg, "-i", uri, "-codec",
                      "copy", "-f", "mpegts", "pipe:1"]

        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stop_thread = False

        def log_output(stderr, stop):  # pragma: no cover
            if config.verbose > 0:
                logger = logging.getLogger("ffmpeg")
                while not stop():
                    try:
                        line = stderr.readline().decode('utf-8').rstrip()
                        if line != '':
                            logger.info(line)
                    except:
                        pass

        t = threading.Thread(target=log_output, args=(
            ffmpeg_proc.stderr, lambda: stop_thread))
        t.setDaemon(True)
        t.start()

        def _stream():
            """Streams n bytes from ffmpeg and terminates the ffmpeg subprocess on exceptions (like client disconnecting)

            Yields:
                bytes: raw mpeg bytes from ffmpeg
            """
            while True:
                try:
                    yield ffmpeg_proc.stdout.read(config.bytes_per_read)
                except:
                    ffmpeg_proc.terminate()
                    ffmpeg_proc.communicate()
                    stop_thread = True
                    break

        return Response(_stream(), content_type='video/mpeg; codecs="avc1.4D401E')
    return app

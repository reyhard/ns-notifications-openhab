"""
NS/public transports delay/disruption API
"""
import json
import logging

from flask import Flask, jsonify, render_template, request
from pymemcache.client import Client as MemcacheClient
from werkzeug.debug import get_current_traceback

app = Flask(__name__)

# create logger
logger = logging.getLogger('nsapi_server')
logger.setLevel(logging.DEBUG)
# create file handler which logs even debug messages
fh = logging.FileHandler('nsapi_server.log')
fh.setLevel(logging.DEBUG)
# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.ERROR)
# create formatter and add it to the handlers
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
ch.setFormatter(formatter)
# add the handlers to the logger
logger.addHandler(fh)
logger.addHandler(ch)

## Helper functions for memcache serialisation
def json_serializer(key, value):
    if type(value) == str:
        return value, 1
    #if issubclass(value, ns_api.BaseObject):
    #    print ("instance of NS-API object")
    #    return value.to_json(), 3
    return json.dumps(value), 2


def json_deserializer(key, value, flags):
    if flags == 1:
        return value
    if flags == 2:
        return json.loads(value)
    raise Exception("Unknown serialization format")


# Connect to the Memcache daemon
mc = MemcacheClient(('127.0.0.1', 11211), serializer=json_serializer,
                    deserializer=json_deserializer)


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html', error=e), 404


@app.route('/')
def index():
    return ''


@app.route('/api/')
def api_index():
    return jsonify({'message': 'Oh, hai!'})


@app.route('/<userkey>/')
def user_dashboard(userkey):
    logger.info('[%s][status] nsapi_run: %s', request.remote_addr, mc.get('nsapi_run'))
    result = {}
    #result.append('<html><head><title>NS Storingen</title></head><body>')
    #result.append('<h2>NS api status</h2>')
    try:
        should_run = mc.get('nsapi_run')
        result['nsapi_run'] = "%s" % mc['nsapi_run']
    except KeyError:
        result['nsapi_run'] = "nsapi_run not found"
    result['disruptions'] = []
    try:
        prev_disruptions = mc.get('prev_disruptions')
        disruptions = ns_api.list_from_json(prev_disruptions['unplanned'])
        for disruption in disruptions:
            message = format_disruption(disruption)
            logger.debug(message)
            disruptions.append(message)
    except TypeError:
        #result.append('No disruptions found')
        track = get_current_traceback(skip=1, show_hidden_frames=True,
                                      ignore_system_exceptions=False)
        track.log()
        #abort(500)
    #result.append('<h2>Delays</h2>')
    result['delays'] = []
    try:
        prev_delays = mc.get('1_trips')
        delays = ns_api.list_from_json(prev_delays)
        for delay in delays:
            message = format_trip(delay)
            if message['message']:
                result['delays'].append(message)
    except TypeError:
        #result.append('No trips found')
        track = get_current_traceback(skip=1, show_hidden_frames=True,
                                      ignore_system_exceptions=False)
        track.log()
        #abort(500)
    #result.append('</body></html>')
    #return u'\n'.join(result)
    return render_template('status.html', content=result)


@app.route('/api/<userkey>/')
def api_user(userkey):
    return jsonify({})


@app.route('/api/<userkey>/listroutes')
def api_list_routes(userkey):
    """List all routes (trajectories) in the user's settings, including some info on them"""
    data = {}
    try:
        data['routegroups'] = settings.userconfigs[userkey]['routegroups']
    except KeyError:
        data['message'] = 'No configuration found for this userkey'
    return jsonify(data)


@app.route('/api/<userkey>/nearby/<lat>/<lon>/json')
def get_nearby_stations(userkey, lat, lon):
    """Look up nearby stations based on lat lon coordinates"""
    return jsonify({'message': 'Not implemented yet'})


@app.route('/api/<userkey>/disable/<location>')
def disable_notifier(userkey, location=None):
    location_prefix = '[{0}][location: {1}]'.format(request.remote_addr, location)
    try:
        should_run = mc.get('nsapi_run')
        logger.info('%s nsapi_run was %s, disabling' % (location_prefix, should_run))
    except KeyError:
        logger.info('%s no nsapi_run tuple in memcache, creating with value False' % location_prefix)
    mc.set('nsapi_run', False, MEMCACHE_DISABLING_TTL)
    return jsonify({'message': 'Disabling notifications', 'location': location})


@app.route('/api/<userkey>/enable/<location>')
def enable_notifier(userkey, location=None):
    location_prefix = '[{0}][location: {1}]'.format(request.remote_addr, location)
    try:
        should_run = mc.get('nsapi_run')
        logger.info('%s nsapi_run was %s, enabling' % (location_prefix, should_run))
    except KeyError:
        logger.info('%s no nsapi_run tuple in memcache, creating with value True' % location_prefix)
    mc.set('nsapi_run', True, MEMCACHE_DISABLING_TTL)
    return jsonify({'message': 'Enabling notifications', 'location': location})


@app.route('/<userkey>/listroutes')
def list_routes(userkey):
    """List all routes (trajectories) in the user's settings, including some info on them"""
    data = {}
    try:
        data['routegroups'] = settings.userconfigs[userkey]['routegroups']
    except KeyError:
        data['message'] = 'No configuration found for this userkey'
    return render_template('routes.html', data=data)


if __name__ == '__main__':
    # Run on public interface (!) on non-80 port
    app.run(host='0.0.0.0', port=8086, debug=True)

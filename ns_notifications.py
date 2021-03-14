 # -*- coding: utf-8 -*-
"""
NS trip notifier
"""
import ns_api
import click
from pymemcache.client import Client as MemcacheClient
import datetime
import json
import requests
import socket
import __main__ as main
import logging
import sys
import os

from openhab import OpenHAB

from configparser import ConfigParser
#try:
#    import settings
#except ImportError:
#    print('Copy settings_example.py to settings.py and set the configuration to your own preferences')
#    sys.exit(1)


# Only plan routes that are at maximum half an hour in the past or an hour in the future
MAX_TIME_PAST = 1800
MAX_TIME_FUTURE = 3600

# Set max time to live for a key to an hour
MEMCACHE_TTL = 3600
MEMCACHE_VERSIONCHECK_TTL = 3600 * 12
MEMCACHE_DISABLING_TTL = 3600 * 6

VERSION_NSAPI = '3.0.5'


class MemcachedNotInstalledException(Exception):
    pass


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

def get_config(config_dir):
    # Load configuration file
    config = ConfigParser(delimiters=('=', ))
    config.optionxform = str
    config.read([os.path.join(config_dir, 'config.ini.dist'), os.path.join(config_dir, 'config.ini')])
    return config

## Check for an update of the notifier
def get_repo_version():
    """
    Get the current version on GitHub
    """
    url = 'https://raw.githubusercontent.com/reyhard/ns-notifications-openhab/master/VERSION'
    try:
        response = requests.get(url)
        if response.status_code != 404:
            return response.text.replace('\n', '')
    except requests.exceptions.ConnectionError:
        #return -1
        return None
    return None


def get_local_version():
    """
    Get the locally installed version
    """
    with open ("VERSION", "r") as versionfile:
        return versionfile.read().replace('\n', '')


def check_versions(mc):
    """
    Check whether version of ns-notifier is up-to-date and ns-api is latest version too
    """
    message = {'header': 'ns-notifications needs updating', 'message': None}
    current_version = None
    try:
        version = mc.get('ns-notifier_version')
    except socket.error:
        raise MemcachedNotInstalledException
    if not version:
        version = get_repo_version()
        current_version = get_local_version()
        if not version:
            # 404 or timeout on remote VERSION file, refresh with current_version
            mc.set('ns-notifier_version', current_version, MEMCACHE_VERSIONCHECK_TTL)
        elif version != current_version:
            message['message'] = 'Current version: ' + str(current_version) + '\nNew version: ' + str(version)
            mc.set('ns-notifier_version', version, MEMCACHE_VERSIONCHECK_TTL)

    version = mc.get('ns-api_version')
    if not version:
        if ns_api.__version__ != VERSION_NSAPI:
            # ns-api needs updating
            if message['message']:
                message['message'] = message['message'] + '\n'
            else:
                message['message'] = ''
            message['message'] = message['message'] + 'ns-api needs updating'
            mc.set('ns-api_version', VERSION_NSAPI, MEMCACHE_VERSIONCHECK_TTL)

    if not message['message']:
        # No updating needed, return None object
        message = None
    return message


## Often-used handles
def get_logger():
    """
    Create logging handler
    """
    ## Create logger
    logger = logging.getLogger('ns_notifications')
    logger.setLevel(logging.DEBUG)
    # create file handler which logs even debug messages
    fh = logging.FileHandler('ns_notifications.log')
    fh.setLevel(logging.DEBUG)
    # create formatter and add it to the handlers
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    # add the handlers to the logger
    logger.addHandler(fh)
    return logger

## Format messages
def format_disruption(disruption):
    """
    Format a disruption on a trajectory
    """
    print(disruption.key)
    print(disruption.line)
    print(disruption.timestamp)
    #print(disruption.disruption)
    time = disruption.timestamp
    if time != None:
        time = ns_api.simple_time(time)
    return {'timestamp': time, 'header': u'Traject: ' + disruption.line, 'message': u'⚠ ' + str(disruption.disruption)}
    #return {'header': 'Traject: ' + disruption.line, 'message': disruption.reason + "\n" + disruption.message}


def format_trip(trip, text_type='long'):
    """
    Format a Trip, providing an overview of all events (delays, messages etc)

    text_type: (long|symbol)
    """
    trip_delay = trip.delay
    message = u''
    if trip_delay['requested_differs']:
        #message = message + u'↦ ' + ns_api.simple_time(trip_delay['requested_differs']) + u' (' + ns_api.simple_time(trip.requested_time)
        message = message + u'↦ ' + ns_api.simple_time(trip.requested_time)
    if trip_delay['departure_delay']:
        #message = message + u' 🕖 ' + ns_api.simple_time(trip_delay['departure_delay']) +")\n"
        message = message + u' +' + ns_api.simple_time(trip_delay['departure_delay']) +"\n"
    if trip.arrival_time_actual != trip.arrival_time_planned:
        #message = message + u'⇥ ' + ns_api.simple_time(trip.arrival_time_actual) + u' (' + ns_api.simple_time(trip.arrival_time_planned) + u' 🕖 ' + ns_api.simple_time(trip.arrival_time_actual - trip.arrival_time_planned) + ")\n"
        message = message + u'⇥ ' + ns_api.simple_time(trip.arrival_time_planned) + u' +' + ns_api.simple_time(trip.arrival_time_actual - trip.arrival_time_planned) + "\n"

    if trip.trip_remarks:
        for remark in trip.trip_remarks:
            if remark.is_grave:
                message = u'⚠ ' + message + remark.message + '\n'
            else:
                message = u'★ ' + message + remark.message + '\n'

    subtrips = []
    for part in trip.trip_parts:
        if part.has_delay:
            #subtrips.append(part.transport_type + ' naar ' + part.destination + ' van ' + ns_api.simple_time(part.departure_time) + ' vertrekt van spoor ' + part.stops[0].platform)
            subtrips.append(part.transport_type + ' naar ' + part.destination + ' van ' + ns_api.simple_time(part.departure_time) + ' (spoor ' + part.stops[0].platform + ')')
            for stop in part.stops:
                if stop.delay:
                    #subtrips.append('Stop ' + stop.name + ' @ ' + ns_api.simple_time(stop.time) + ' ' + stop.delay)
                    subtrips.append(u'🚉 ' + stop.name + ' @ ' + ns_api.simple_time(stop.time) + ' ' + stop.delay)
    message = message + u'\n'.join(subtrips)
    message = message + '\n\n(ns-notifier)'
    return {'header': trip.trip_parts[0].transport_type + ' ' + trip.departure + '-' + trip.destination + ' (' + ns_api.simple_time(trip.requested_time) + ')', 'message': message}


def get_changed_disruptions(mc, disruptions):
    """
    Get the new or changed disruptions
    """
    #prev_disruptions = None
    prev_disruptions = mc.get('prev_disruptions')
    # TODO: check whether this went ok
    if prev_disruptions == None or prev_disruptions == []:
        prev_disruptions = {'unplanned': [], 'planned': []}

    prev_disruptions_unplanned = ns_api.list_from_json(prev_disruptions['unplanned'])
    new_or_changed_unplanned = ns_api.list_diff(prev_disruptions_unplanned, disruptions['unplanned'])
    save_unplanned = ns_api.list_merge(prev_disruptions_unplanned, new_or_changed_unplanned)

    try:
        keywordfilter = settings.keywordfilter
    except AttributeError:
        keywordfilter = []
    # filter away on keyword
    save_unplanned_filtered = []
    for item in save_unplanned:
        for keyword in keywordfilter:
            if keyword not in item:
                save_unplanned_filtered.append(item)

    # Planned disruptions don't have machine-readable date/time and route information, so
    # we skip planned disruptions for this moment
    #new_or_changed_planned = ns_api.list_diff(prev_disruptions['planned'], disruptions['planned'])
    #print(new_or_changed_planned)
    #for plan in new_or_changed_planned:
    #    print plan.key
    #    print plan.message
    #    print "------"

    #unchanged_planned = ns_api.list_same(prev_disruptions['planned'], disruptions['planned'])
    #prev_planned = new_or_changed_planned + unchanged_planned

    # Update the cached list with the current information
    mc.set('prev_disruptions', {'unplanned': ns_api.list_to_json(save_unplanned_filtered), 'planned': []}, MEMCACHE_TTL)
    return new_or_changed_unplanned


def get_changed_trips(mc, nsapi, routes, userkey):
    """
    Get the new or changed trips for userkey
    """
    today = datetime.datetime.now().strftime('%d-%m')
    today_date = datetime.datetime.now().strftime('%d-%m-%Y')
    current_time = datetime.datetime.now()

    prev_trips = mc.get(str(userkey) + '_trips')
    if prev_trips == None:
        prev_trips = []
    prev_trips = ns_api.list_from_json(prev_trips)
    trips = []
    for route in routes:
        if len(route['time']) <= 5:
            route_time = datetime.datetime.strptime(today_date + " " + route['time'], "%d-%m-%Y %H:%M")
        else:
            route_time = datetime.datetime.strptime(route['time'], "%d-%m-%Y %H:%M")
        delta = current_time - route_time
        if current_time > route_time and delta.total_seconds() > MAX_TIME_PAST:
            # the route was too long ago ago, lets skip it
            continue
        if current_time < route_time and abs(delta.total_seconds()) > MAX_TIME_FUTURE:
            # the route is too much in the future, lets skip it
            continue
        try:
            keyword = route['keyword']
        except KeyError:
            keyword = None
        current_trips = nsapi.get_trips(route['time'], route['departure'], keyword, route['destination'], True)
        optimal_trip = ns_api.Trip.get_actual(current_trips, route['time'])
        for trip in current_trips:
            print(trip.departure_time_planned)
            if(trip.status == "NORMAL"):
                print("according to plan captain!")
            print(trip.delay)
            print(trip.status)
            print(trip.departure_platform_actual)


        #optimal_trip = ns_api.Trip.get_optimal(current_trips)
        if not optimal_trip:
            #print("Optimal not found. Alert?")
            # TODO: Get the trip before and the one after route['time']?
            pass
        else:
            try:
                # User set a minimum treshold for departure, skip if within this limit
                minimal_delay = int(route['minimum'])
                trip_delay = optimal_trip.delay
                if (not optimal_trip.has_delay) or (optimal_trip.has_delay and trip_delay['departure_delay'] != None and trip_delay['departure_delay'].seconds//60 < minimal_delay and optimal_trip.going):
                    # Trip is going, has no delay or one that is below threshold, ignore
                    optimal_trip = None
            except KeyError:
                # No 'minimum' setting found, just continue
                pass
        if optimal_trip:
            trips.append(optimal_trip)
        #print(optimal_trip)

    new_or_changed_trips = ns_api.list_diff(prev_trips, trips)
    #prev_trips = new_or_changed_trips + trips
    save_trips = ns_api.list_merge(prev_trips, trips)

    mc.set(str(userkey) + '_trips', ns_api.list_to_json(save_trips), MEMCACHE_TTL)
    return new_or_changed_trips

## Main program
@click.group()
def cli():
    """
    NS-Notifications
    """
    #run_all_notifications()
    #print 'right'
    pass

@cli.command()
@click.option('--departure',
              required=True,
              help=(('Departure station, '
                     'if not specifed, it is read from --device-config')))
@click.option('--destination',
              required=True,
              help=(('Destination station, '
                     'if not specifed, it is read from --device-config')))
@click.option('--time',
              required=False,
              default=None,
              help=(('Departure time, '
                     'if not specifed, it is read from --device-config')))
@click.option('--config_dir',
              required=False,
              default=sys.path[0],
              help=(('Config directory, '
                     'Directory where config.ini is located')))
def check_connections(departure, destination, time, config_dir):
    """
    Send 'ns-notifcations was updated' message after (automatic) upgrade
    """

    settings = get_config(config_dir)
    nsapi = ns_api.NSAPI( settings['General'].get('apikey',''))
    print(departure + " " + destination + " " + str(time))
    

    openhab = OpenHAB(settings['Openhab'].get('openhab_url'))
    item_ns_routeName = openhab.get_item(settings['Openhab'].get('openhab_item_route_name'))
    item_ns_routeName.command(departure + "->" + destination + " (" + str(time)+")")
    current_trips = nsapi.get_trips(time, departure, None, destination, True)
    ns_trains = json.loads(settings['Openhab'].get('openhab_item_trains',[]))

    for index, trip in enumerate(current_trips):
        print(index)
        if(trip.status == "NORMAL"):
            text = "🟢 "
        else:
            text = "🔴 "
        text = text + str(trip.product_shortCategoryName) + " "
        text = text + "  " + str(ns_api.simple_time(trip.departure_time_planned))
        text = text + " ➡ " + str(ns_api.simple_time(trip.arrival_time_planned))
        text = text + " ⏱ " + (str(datetime.timedelta(minutes=(trip.travel_time_actual))))[:-3]
        
        if(trip.status == "NORMAL"):
            print("according to plan captain!")
        else:
            print(trip.disruptions_head)
            print(trip.disruptions_text)
        #print(trip.delay)
        print(trip.status)
        print(trip.departure_platform_actual)
        item_ns_train = openhab.get_item(ns_trains[index])
        item_ns_train.command(text)

#@cli.command('run_all_notifications')
@cli.command()
@click.option('--config_dir',
              required=False,
              default=sys.path[0],
              help=(('Config directory, '
                     'Directory where config.ini is located')))
def run_all_notifications(config_dir):
    """
    Check for both disruptions and configured trips
    """
    logger = get_logger()
    settings = get_config(config_dir)

    ## Open memcache
    mc = MemcacheClient(('127.0.0.1', 11211), serializer=json_serializer,
            deserializer=json_deserializer)

    ## Check whether there's a new version of this notifier
    update_message = check_versions(mc)
    try:
        if update_message and settings.auto_update:
            # Create (touch) file that the run_notifier script checks on for 'update needed'
            open(os.path.dirname(os.path.realpath(__file__)) + '/needs_updating', 'a').close()
            update_message = None
    except AttributeError:
        # 'auto_update' likely not defined in settings.py, default to False
        pass

    ## NS Notifier userkey (will come from url/cli parameter in the future)
    try:
        userkey = settings.userkey
    except AttributeError:
        userkey = 1


    ## Are we planned to run? (E.g., not disabled through web)
    try:
        should_run = mc.get('nsapi_run')
    except:
        should_run = True
    if should_run == None:
        should_run = True
        #logger.info('no run tuple in memcache, creating')
        mc.set('nsapi_run', should_run, MEMCACHE_DISABLING_TTL)


    # HACK, change when moved to Click and parameters
    try:
        if settings.skip_trips == True and settings.skip_disruptions == False:
            should_run = True
    except AttributeError:
        logger.error('Tried overriding should_run, but no skip_* found')

    #print('should run? ' + str(should_run))
    logger.debug('Should run: ' + str(should_run))

    if not should_run:
        sys.exit(0)

    errors = []
    nsapi = ns_api.NSAPI( settings.apikey)

    ## Get the list of stations
    #stations = get_stations(mc, nsapi)


    ## Get the current disruptions (globally)
    changed_disruptions = []
    get_disruptions = True
    try:
        if settings.skip_disruptions:
            get_disruptions = False
    except AttributeError:
        logger.error('Missing skip_disruptions setting')
    if get_disruptions:
        try:
            disruptions = nsapi.get_disruptions()
            changed_disruptions = get_changed_disruptions(mc, disruptions)
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
            #print('[ERROR] connectionerror doing disruptions')
            logger.error('Exception doing disruptions ' + repr(e))
            errors.append(('Exception doing disruptions', e))

    ## Get the information on the list of trips configured by the user
    trips = []
    get_trips = True
    try:
        if settings.skip_trips:
            get_trips = False
    except AttributeError:
        logger.error('Missing skip_trips setting')
    if get_trips:
        try:
            trips = get_changed_trips(mc, nsapi, settings.routes, userkey)
            print(trips)
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
            #print('[ERROR] connectionerror doing trips')
            logger.error('Exception doing trips ' + repr(e))
            errors.append(('Exception doing trips', e))

    # User is interested in arrival delays
    arrival_delays = True
    try:
        arrival_delays = settings.arrival_delays
    except AttributeError:
        pass

    if settings.notification_type == 'pb':
        #p, sendto_device = get_pushbullet_config(logger)
        #if not sendto_device:
        #    sys.exit(1)

        #if update_message:
        #    p.push_note(update_message['header'], update_message['message'], sendto_device)

        if changed_disruptions:
            # There are disruptions that are new or changed since last run
            sendto_channel = None

            for disruption in changed_disruptions:
                message = format_disruption(disruption)
        if trips:
            for trip in trips:
                if not arrival_delays:
                    # User is only interested in departure
                    notification_needed = trip.has_delay(arrival_check=False)
                else:
                    notification_needed = trip.has_delay()
                if notification_needed:
                    message = format_trip(trip)
                    #print message
                    logger.debug(message)
                    #p.push_note('title', 'body', sendto_device)
                    #p.push_note(message['header'], message['message'], sendto_device)

@cli.command()
@click.option('--config_dir',
              required=False,
              default=sys.path[0],
              help=(('Config directory, '
                     'Directory where config.ini is located')))
def updated(config_dir):
    """
    Send 'ns-notifcations was updated' message after (automatic) upgrade
    """
    
    settings = get_config(config_dir)

    openhab = OpenHAB(settings.openhab_url)
    item_ns_notification = openhab.get_item(settings.openhab_item_notifications)

    local_version = get_local_version()
    item_ns_notification.command('Notifier was updated to ' + local_version + ', details might be in your (cron) email')


if not hasattr(main, '__file__'):
    """
    Running in interactive mode in the Python shell
    """
    print("NS Notifier running interactively in Python shell")

elif __name__ == '__main__':
    """
    NS Notifier is ran standalone, rock and roll
    """
    cli()
    #run_all_notifications()

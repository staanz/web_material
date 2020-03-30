"""
@uthor: Shanthanu Varma
mixpandas.py
A library to request data from Mixpanel's Raw Data Export API
If running Python V2, urllib library won't work, neither will parse. Need to use
the V2 equivalent
"""
import datetime
import hashlib
import ast
import urllib.request, urllib.parse, urllib.error
import time
try:
    import json
except ImportError:
    import simplejson as json
import pandas as pd


VERSION = '2.0'  # Mixpanel API version
date_format = '%Y-%m-%d'  # Mixpanel's API date format


def read_events(keys=("API_KEY", "API_SECRET"), events=None, start=None, end=None,
                where=None, bucket=None, columns=None, exclude_mp=True):
    """
    Request data from Mixpanel's Raw Data Export API and return as a pandas
    DataFrame with event times converted to pandas Timestamp objects
    Parameters
    ----------
    keys : tuple containing (Mixpanel API Key, Mixpanel Secret Key)
    events : event name or list of event names to get, optional
        If not specified or None, all events will be downloaded
    start : start date. String or datetime-like, default 2011-07-10
        The input is converted to a date using pandas.to_datetime which
        accepts a variety of inputs, e.g., '5/6/2013', '2013-05-06',
        'May 6, 2013'.  It can also be a datetime object
        The default comes from the earliest start date allowed by the API
    end: end date. String or datetime-like, default yesterday
        The input is converted in the same manner as start. The latest this
        date can be is yesterday's date, which is the default value.
    where: string, Mixpanel filter expression, optional
        See the documentation:
        https://mixpanel.com/docs/api-documentation/data-export-api#segmentation-expressions
    bucket: string, optional
        See the documentation:
        https://mixpanel.com/docs/api-documentation/displaying-mixpanel-data-to-your-users
    columns: string or list of strings, optional
        Returned DataFrame will only contain the specified parameters
    exclude_mp: Filter out Mixpanel-specific data. default True
        Filter out event properties that begin with '$' or 'mp_'.
        These properties are automatically inserted by Mixpanel and indicate
        things like region, OS, etc.
    For more information, see:
    https://mixpanel.com/docs/api-documentation/exporting-raw-data-you-inserted-into-mixpanel
    """

    if start is None:
        # This default comes from an error message you'll receive if you
        # try an start date earlier than 7/10/2011
        start = datetime.date(2011, 0o7, 10)
    start = pd.to_datetime(start)
    start_str = start.strftime(date_format)

    if end is None:  # Defaults to yesterday, the latest allowed
        end = datetime.date.today() - datetime.timedelta(1)
    end = pd.to_datetime(end)
    end_str = end.strftime(date_format)

    payload = {
        'from_date': start_str,
        'to_date': end_str,
    }

    # Handle when single event passed in as string
    if isinstance(events, str):
        events = [events]

    # Fill the payload with the parameters if they're specified
    params = {'event': events, 'where': where, 'bucket': bucket}
    for k, v in params.items():
        if v is not None:
            payload[k] = v

    data = request(keys, ['export'], payload, data_api=True)

    return _export_to_df(data, columns, exclude_mp)


def read_people(keys=("API_KEY", "API_SECRET"), params=None):
    data_json = request(keys, ['engage'], params)
    results = data_json['results']
    session_id = data_json['session_id']
    if 'output_properties' in params.keys():
        columns = ast.literal_eval(params['output_properties'])
    else:
        columns = []
        for k,v in results[0].items():
            if not k == '$properties':
                columns.append(k)
            else:
                for prop in v.keys():
                    columns.append(prop)
    data_df = _people_to_df(results, columns)
    page_size = data_json['page_size']
    len_results = len(results)
    page = data_json['page']
    while len_results == page_size:
        params['session_id'] = session_id
        params['page'] = page+1
        print('doing page ', page+1)
        data_json = request(keys, ['engage'], params)
        results = data_json['results']
        session_id = data_json['session_id']
        len_results = len(results)
        if len_results == 0:
            break
        page = data_json['page']
        temp_data = _people_to_df(results, columns)
        data_df = data_df.append(temp_data).reset_index(drop=True)
    return data_df


def _people_to_df(results, columns):
    data_df = {}
    for column in columns:
        data_df[column] = []
    for result in results:
        temp_dict = {}
        for k,v in result.items():
            if type(v) == str:
                temp_dict[k] = v
            else:
                for prop, val in v.items():
                    temp_dict[prop] = val
        for k in data_df.keys():
            if k in temp_dict.keys():
                data_df[k].append(temp_dict[k])
            else:
                data_df[k].append('nil')
    data_df = pd.DataFrame(data_df)
    return data_df


def _export_to_df(data, columns, exclude_mp):
    # Keep track of the parameters each returned event
    parameters = set()

    # Calls to the data export API do not return JSON.  They return
    # records separated by newlines, where each record is valid JSON.
    # The event parameters are in the properties field
    events = []
    for line in data.split(b'\n'):
        try:
            event = json.loads(line)
            ev = event['properties']
            ev['event'] = event['event']
        except ValueError:  # Not valid JSON
            continue

        parameters.update(list(ev.keys()))
        events.append(ev)

    # If columns is excluded, leave off parameters that start with '$' as
    # these are automatically included in the Mixpanel events and clutter the
    # real data
    if columns is None:
        if exclude_mp:
            columns = [p for p in parameters if not (p.startswith('$') or
                                                     p.startswith('mp_'))]
        else:
            columns = parameters
    elif 'time' not in columns:
        columns.append('time')
    df = pd.DataFrame(events, columns=columns)

    # Make time a datetime.
    df['time'] = df['time'].map(lambda x: datetime.datetime.fromtimestamp(x))

    return df


# The code below is from Mixpanel's Python client for the data export API.
# There are only a few modifications:
#     * Add data_api optional argument
#     * Change the base URL to http://data.mixpanel.com if data_api is set
#     * make it a function that takes keys instead of a class initialized
#       with the keys (this is just a personal preference)
#     * Changed max. line width
# Mixpanel, Inc. -- http://mixpanel.com/
#
# Python API client library to consume mixpanel.com analytics data.
# https://mixpanel.com/site_media//api/v2/mixpanel.py
def request(keys, methods, params, format='json', data_api=False):
    """
        methods - List of methods to be joined,
                      e.g. ['events', 'properties', 'values']
                  will give us
                      http://mixpanel.com/api/2.0/events/properties/values/
        params - Extra parameters associated with method
    """
    api_key, api_secret = keys

    params['api_key'] = api_key
    params['expire'] = int(time.time()) + 600   # Grant this request 10 minutes.
    params['format'] = format
    if 'sig' in params: del params['sig']
    params['sig'] = hash_args(params, api_secret)

    if data_api:
        url_base = r'http://data.mixpanel.com/api'
    else:
        url_base = r'http://mixpanel.com/api'

    request_url = ('/'.join([url_base, str(VERSION)] + methods) + '/?' +
                   unicode_urlencode(params))

    request = urllib.request.urlopen(request_url)
    data = request.read()

    if data_api:
        return data

    return json.loads(data)


def unicode_urlencode(params):
    """
        Convert lists to JSON encoded strings, and correctly handle any
        unicode URL parameters.
    """
    if isinstance(params, dict):
        params = list(params.items())
    for i, param in enumerate(params):
        if isinstance(param[1], list):
            params[i] = (param[0], json.dumps(param[1]),)

    return urllib.parse.urlencode(
        [(k, isinstance(v, str) and v.encode('utf-8') or v)
            for k, v in params]
    )


def hash_args(args, api_secret):
    """
        Hashes arguments by joining key=value pairs, appending a secret, and
        then taking the MD5 hex digest.
    """
    for a in args:
        if isinstance(args[a], list): args[a] = json.dumps(args[a])

    args_joined = b''
    for a in sorted(args.keys()):
        if isinstance(a, str):
            args_joined += a.encode('utf-8')
        else:
            args_joined += str(a).encode('utf-8')

        args_joined += b'='

        if isinstance(args[a], str):
            args_joined += args[a].encode('utf-8')
        else:
            args_joined += str(args[a]).encode('utf-8')

    hash = hashlib.md5(args_joined)

    hash.update(api_secret.encode('utf-8'))
    return hash.hexdigest()

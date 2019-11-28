#!/usr/bin/env python
 
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg2
import os
import sys
import numpy as np
from gpxplotter.gpxread import vincenty
from collections import namedtuple
from jinja2 import Template
import urllib
import pendulum
import datetime

csv_template = """id,start,end,odo,time,dist,speed,soc,d_soc,rng_ideal,d_rng_ideal,rng_est,d_rng_est,rng_rated,d_rng_rated,energy_lap,energy_hour,energy_left,t_in,t_out
{% for item in items -%}
{{item.id}},{{item.start_time}},{{item.end_time}},{{item.odo}},{{item.lap_time}},{{'%.2f' % item.lap_dist}},{{'%.2f' % item.lap_speed}},{{'%.2f' % item.soc}},{{'%.2f' % item.d_soc}},{{'%.2f' % item.rng_ideal}},{{'%.2f' % item.d_rng_ideal}},{{'%.2f' % item.rng_est}},{{'%.2f' % item.d_rng_est}},{{'%.2f' % item.rng_rated}}},{{'%.2f' % item.d_rng_rated}},{{'%.2f' % item.d_energy}},{{'%.2f' % item.energy_hour}},{{'%.2f' % item.energy_left}},{{'%.2f' % item.t_in}},{{'%.2f' % item.t_out}}
{% endfor %}
"""

html_template = """
<html><body>
<table border="1">
<TR>
   <TH>id</th>
   <TH>start</th>
   <TH>end</th>
   <TH>odo[km]</th>
   <TH>soc[%]</th>
   <TH>rng_rated[km]</th>
   <TH>time</th>
   <TH>dist[km]</th>
   <TH>d_rng_rated[km]</th>
   <TH>speed[km/h]</th>
   <TH>energy/lap[kW]</th>
   <TH>energy/hour[kW]</th>
   <TH>energy_left[kW]</th>
   <TH>t_out[C]</th>
</TR>
{% for item in items %}
<TR>
   <TD>{{item.id}}</TD>
   <TD>{{item.start_time}}</TD>
   <TD>{{item.end_time}}</TD>
   <TD>{{'%.2f' % item.odo}}</TD>
   <TD>{{'%.2f' % item.soc}}</TD>
   <TD>{{'%.2f' % item.rng_rated}}</TD>
   <TD>{{item.lap_time}}</TD>
   <TD>{{'%.2f' % item.lap_dist}}</TD>
   <TD>{{'%.2f' % item.d_rng_rated}}</TD>
   <TD>{{'%.2f' % item.lap_speed}}</TD>
   <TD>{{'%.2f' % item.d_energy}}</TD>
   <TD>{{'%.2f' % item.energy_hour}}</TD>
   <TD>{{'%.2f' % item.energy_left}}</TD>
   <TD>{{'%.2f' % item.t_out}}</TD>
</TR>
{% endfor %}
</table>
</body></html>
"""

# HTTPRequestHandler class
class testHTTPServer_RequestHandler(BaseHTTPRequestHandler):
 
  # GET
  def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        config = {
            "lat": float(query['lat'][0]) if 'lat' in query else None,
            "lon": float(query['lon'][0]) if 'lon' in query else None,
            "radius": float(query['radius'][0]) if 'radius' in query else 100,
            "consumption_rated": float(query['consumption_rated'][0]) if 'consumption_rated' in query else 14.7,
            "hours": int(query['hours'][0]) if 'hours' in query else 24,
            "format": query['format'][0] if 'format' in query else None,
            "from_time": pendulum.parse(query['from_time'][0],tz='Europe/Prague') if 'from_time' in query else None,
            "merge_from_lap": int(query['merge_from_lap'][0]) if 'merge_from_lap' in query else 1,
            "lap_merge": int(query['lap_merge'][0]) if 'lap_merge' in query else 1,
        }
        # Send response status code
        self.send_response(200)
 
        # Send headers
        self.send_header('Content-type', 'text/plain' if config["format"] == 'csv' else 'text/html')
        self.end_headers()
 
        # Send message back to client
        db_data = do_db_stuff(config)
        laps = find_laps(config, db_data, config['radius'], 0, -1)
        t = Template(csv_template if config["format"] == 'csv' else html_template)
        message = t.render(items=laps)
        # Write content as utf-8 data
        self.wfile.write(bytes(message, "utf8"))
        return
 
def run():
  print('starting server...')
 
  # Server settings
  # Choose port 8080, for port 80, which is normally used for a http server, you need root access
  server_address = ('', 8000)
  httpd = HTTPServer(server_address, testHTTPServer_RequestHandler)
  print('running server...')
  httpd.serve_forever()

def do_db_stuff(config):
    conn = None
    out_data = []
    try:
        hostname = "database"
        login = os.environ.get("TM_DB_USER", "teslamate")
        password = os.environ.get("TM_DB_PASS", "secret")
        db_name =  os.environ.get("TM_DB_NAME", "teslamate")
        print('Connecting to the PostgreSQL database...')
        conn = psycopg2.connect(host=hostname,database=db_name, user=login, password=password)
        cur = conn.cursor()
        if config["from_time"] is not None:
            cur.execute("SELECT * FROM positions where date >= %s::timestamptz ORDER BY date", (datetime.datetime.fromtimestamp(config['from_time'].in_tz('utc').timestamp()),))
        else:
            cur.execute("SELECT * FROM positions where date between (now() - '%s hour'::interval) and (now() - '%s hour'::interval) ORDER BY date", (config['hours'], 0))
        print("The number of parts: ", cur.rowcount)

        rdef = namedtuple('dataset', ' '.join([x[0] for x in cur.description])) 
        for row in map(rdef._make, cur.fetchall()): 
            out_data.append(row)
            row = cur.fetchone()
 
        cur.close()
    except (Exception, psycopg2.DatabaseError) as error:
        print(error)
    finally:
        if conn is not None:
            conn.close()
    return out_data

def find_laps(config, segment, region=10, min_time=5, start_idx=0):
    """Return laps given latitude & longitude data.

    We assume that the first point defines the start and
    that the last point defines the end and that these are
    approximately at the same place.

    Parameters
    ----------
    segment : dict
        The data for the full track.
    region : float
        The region around the starting point which is used to
        define if we are passing through the starting point and
        begin a new lap.
    min_time : float
        This is the minimum time (in seconds) we should spend in the
        region before exiting. This will depend on the setting for region
        and the velocity for the activity.
    start_idx : integer
        The starting point for the first lap.

    """
    points = [(pt.latitude, pt.longitude) for pt in segment]
    start = (config['lat'], config["lon"]) if config['lat'] is not None and config["lon"] else points[start_idx]
    time = [pt.date for pt in segment]
    # For locating the tracks, we look for point which are such that
    # we enter the starting region and pass through it.
    # For each point, find the distance to the starting region:
    distance = np.array([vincenty(point, start) for point in points])
    # Now look for points where we enter the region:
    # We want to avoid cases where we jump back and forth across the
    # boundary, so we set a minimum time we should spend inside the
    # region.
    enter_point = []
    current = start_idx
    in_region = True
    for i, dist in enumerate(distance):
        if i <= start_idx:
            continue
        if dist < region:  # we are inside the region
            if distance[i-1] > region:  # came from the outside
                current = i
                in_region = True
        if in_region:
            if dist > region:
                delta_t = time[i] - time[current]
                if min_time < delta_t.total_seconds():
                    enter_point.append(current)
                current = None
                in_region = False
    laps = []
    for i, idx in enumerate(enter_point):
        try:
            laps.append({"id": str(i+1), "from": idx, "to": enter_point[i+1]})
        except IndexError:
            laps.append({"id": str(i+1), "from": idx, "to": len(points)-1})
    agg_laps = aggregate_laps(config, laps)
    segment_laps = get_segment_laps(config, segment, agg_laps)
    return segment_laps

def aggregate_laps(config, laps):
    agg_start = config["merge_from_lap"]
    agg_count = config["lap_merge"]

    if agg_count <= 1 or len(laps) <= agg_start:
        return laps

    agg_laps = []

    # copy the ones before start (1, 2, 3, ... agg_start - 1)
    for i in range(agg_start - 1):
        agg_laps.append(laps[i])

    group_count = (len(laps) - agg_start + 1) // agg_count
    for i in range(group_count):
        first = laps[agg_start-1+i*agg_count]
        last = laps[agg_start-1+(i+1)*agg_count - 1]
        lap = {
            "id": "" + first["id"] + "-" + last["id"],
            "from": first["from"],
            "to": last["to"],
        }
        agg_laps.append(lap)

    for i in range(agg_start + agg_count*group_count - 1, len(laps)):
        agg_laps.append(laps[i])
    return agg_laps

def extract_lap_info(config, lap_id, lap_data):
    """ Lap data from database:
    xx id |          
    xx date           | 
    xx latitude  | 
    xx longitude | 
    -- speed | 
    -- power |  
    xx odometer   | 
    xx ideal_battery_range_km | 
    xx battery_level | 
    xx outside_temp | 
    -- elevation | 
    -- fan_status | 
    -- driver_temp_setting | 
    -- passenger_temp_set ting | 
    -- is_climate_on | 
    -- is_rear_defroster_on | 
    -- is_front_defroster_on | 
    -- car_id | 
    -- drive_id | 
    xx inside_temp | 
    -- battery_heater |
    -- battery_heater_on | 
    -- battery_heater_no_power | 
    xx est_battery_range_km | 
    xx rated_battery_range_km
    """
    tz = pendulum.timezone('Europe/Paris')
    sd = pendulum.instance(lap_data[0].date, 'utc')
    ed = pendulum.instance(lap_data[-1].date, 'utc')
    lap_time = lap_data[-1].date - lap_data[0].date
    lap_dist = lap_data[-1].odometer -  lap_data[0].odometer
    return {
        "id": lap_id,
        "start_time": sd.in_tz("Europe/Prague").format("DD.MM.YY HH:mm.ss"),
        "end_time":  ed.in_tz("Europe/Prague").format("DD.MM.YY HH:mm.ss"),
        "odo": lap_data[-1].odometer,
        "t_in": lap_data[-1].inside_temp,
        "t_out": lap_data[-1].outside_temp,
        "lap_time": str(lap_time),
        "lap_speed": lap_dist / lap_time.total_seconds() * 3600,
        "lap_dist": lap_dist,
        "soc": lap_data[-1].battery_level,
        "d_soc": lap_data[0].battery_level -  lap_data[-1].battery_level,
        "rng_ideal": lap_data[-1].ideal_battery_range_km,
        "d_rng_ideal": lap_data[0].ideal_battery_range_km -  lap_data[-1].ideal_battery_range_km,
        "rng_est": lap_data[-1].est_battery_range_km,
        "d_rng_est": lap_data[0].est_battery_range_km -  lap_data[-1].est_battery_range_km,
        "rng_rated": lap_data[-1].rated_battery_range_km,
        "d_rng_rated": lap_data[0].rated_battery_range_km -  lap_data[-1].rated_battery_range_km,
        "d_energy": config["consumption_rated"] / 100 * (lap_data[0].rated_battery_range_km -  lap_data[-1].rated_battery_range_km),
        "energy_hour": (config["consumption_rated"] / 100 * (lap_data[0].rated_battery_range_km -  lap_data[-1].rated_battery_range_km)) / lap_time.total_seconds() * 3600.0,
        "energy_left": config["consumption_rated"] / 100 * lap_data[-1].rated_battery_range_km,
    }


def get_segment_laps(config, segment, laps):
    """Extract the segment laps.  """
    segment_laps = []
    for i, lap in enumerate(laps):
        lap_id = lap["id"]
        start = lap["from"]
        stop = lap["to"] + 1
        lap_data = segment[start:stop]
        new_lap = extract_lap_info(config, lap_id, lap_data)
        segment_laps.append(new_lap)
    return segment_laps



 
run()

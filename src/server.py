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

csv_template = """id,start,end,time,dist,speed,odo_dist,d_soc,d_rng_ideal,d_rng_est,d_rng_rated,energy
{% for item in items -%}
{{item.id}},{{item.start_time}},{{item.end_time}},{{item.lap_time}},{{'%.2f' % item.lap_dist}},{{'%.2f' % item.lap_speed}},{{'%.2f' % item.odo_dist}},{{'%.2f' % item.d_soc}},{{'%.2f' % item.d_rng_ideal}},{{'%.2f' % item.d_rng_est}},{{'%.2f' % item.d_rng_rated}},{{'%.2f' % item.d_energy}}
{% endfor %}
"""

html_template = """
<html><body>
<table border="1">
<TR>
   <TH>id</th>
   <TH>start</th>
   <TH>end</th>
   <TH>time</th>
   <TH>dist[km]</th>
   <TH>speed[km/h]</th>
   <TH>odo_dist[km]</th>
   <TH>d_soc[%]</th>
   <TH>d_rng_ideal[km]</th>
   <TH>d_rng_est[km]</th>
   <TH>d_rng_rated[km]</th>
   <TH>energy[kW]</th>
</TR>
{% for item in items %}
<TR>
   <TD>{{item.id}}</TD>
   <TD>{{item.start_time}}</TD>
   <TD>{{item.end_time}}</TD>
   <TD>{{item.lap_time}}</TD>
   <TD>{{'%.2f' % item.lap_dist}}</TD>
   <TD>{{'%.2f' % item.lap_speed}}</TD>
   <TD>{{'%.2f' % item.odo_dist}}</TD>
   <TD>{{'%.2f' % item.d_soc}}</TD>
   <TD>{{'%.2f' % item.d_rng_ideal}}</TD>
   <TD>{{'%.2f' % item.d_rng_est}}</TD>
   <TD>{{'%.2f' % item.d_rng_rated}}</TD>
   <TD>{{'%.2f' % item.d_energy}}</TD>
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
            laps.append((idx, enter_point[i+1]))
        except IndexError:
            laps.append((idx, len(points)-1))
    segment_laps = get_segment_laps(config, segment, laps)
    return segment_laps


def get_segment_laps(config, segment, laps):
    """Extract the segment laps."""
    segment_laps = []
    for i, lap in enumerate(laps):
        start, stop = lap
        stop += 1
        lap_data = segment[start:stop]
        lap_dist = 0
        for j in range(len(lap_data) - 1):
            a = lap_data[j]
            b = lap_data[j + 1]
            pta = (a.latitude, a. longitude)
            ptb = (b.latitude, b. longitude)
            lap_dist += vincenty(pta, ptb)
        tz = pendulum.timezone('Europe/Paris')
        sd = pendulum.instance(lap_data[0].date, 'utc')
        ed = pendulum.instance(lap_data[-1].date, 'utc')
        lap_time = lap_data[-1].date - lap_data[0].date
        new_lap = {
            "id": i + 1,
            "start_time": sd.in_tz("Europe/Prague").format("DD.MM.YY HH:mm.ss"),
            "end_time":  ed.in_tz("Europe/Prague").format("DD.MM.YY HH:mm.ss"),
            "lap_time": str(lap_time),
            "lap_dist": lap_dist / 1000.0,
            "lap_speed": lap_dist / lap_time.total_seconds() * 3.6,
            "odo_dist": lap_data[-1].odometer -  lap_data[0].odometer,
            "d_soc": lap_data[0].battery_level -  lap_data[-1].battery_level,
            "d_rng_ideal": lap_data[0].ideal_battery_range_km -  lap_data[-1].ideal_battery_range_km,
            "d_rng_est": lap_data[0].est_battery_range_km -  lap_data[-1].est_battery_range_km,
            "d_rng_rated": lap_data[0].rated_battery_range_km -  lap_data[-1].rated_battery_range_km,
            "d_energy": config["consumption_rated"] / 100 * (lap_data[0].rated_battery_range_km -  lap_data[-1].rated_battery_range_km),
        }


        #dataset(, speed=93, power=-43.0,  )


        #for key, val in segment.items():
        #    if key in ('average-hr', 'ele-up', 'ele-down'):
        #        pass
        #    else:
        #        new_lap[key] = val[start:stop]
        #new_lap['average-hr'] = np.average(new_lap['pulse'])
        #new_lap['distance'] = new_lap['distance'] - new_lap['distance'][0]
        #for key in ('time-delta', 'delta-seconds'):
        #    new_lap[key] = [i - new_lap[key][0] for i in new_lap[key]]
        #ele_diff = np.diff(new_lap['ele'])
        #new_lap['ele-up'] = ele_diff[np.where(ele_diff > 0)[0]].sum()
        #new_lap['ele-down'] = ele_diff[np.where(ele_diff < 0)[0]].sum()
        #print('\nData for lap: {}'.format(i+1))
        #print('\tAverage HR: {:6.2f}'.format(new_lap['average-hr']))
        #print('\tDistance (m): {:<9.2f}'.format(new_lap['distance'][-1]))
        #print('\tTime (H:M:S): {}'.format(new_lap['time-delta'][-1]))
        #print('\tElevation gain: {:<9.2f}'.format(new_lap['ele-up']))
        #print('\tElevation drop: {:<9.2f}'.format(new_lap['ele-down']))
        segment_laps.append(new_lap)
    return segment_laps



 
run()

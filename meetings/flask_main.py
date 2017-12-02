import flask
from flask import g
from flask import render_template
from flask import request
from flask import url_for
import uuid
import hashlib
import sys
import json
import logging
from datetime import datetime

# Mongo database
from pymongo import MongoClient
from bson.objectid import ObjectId

# Date handling 
import arrow # Replacement for datetime, based on moment.js
# import datetime # But we still need time
from dateutil import tz  # For interpreting local times


# OAuth2  - Google library implementation for convenience
from oauth2client import client
import httplib2   # used in oauth2 flow

# Google API for services 
from apiclient import discovery

###
# Globals
###
import config
if __name__ == "__main__":
    CONFIG = config.configuration()
else:
    CONFIG = config.configuration(proxied=True)

MONGO_CLIENT_URL = "mongodb://{}:{}@{}:{}/{}".format(
    CONFIG.DB_USER,
    CONFIG.DB_USER_PW,
    CONFIG.DB_HOST,
    CONFIG.DB_PORT,
    CONFIG.DB)


print("Using URL '{}'".format(MONGO_CLIENT_URL))

app = flask.Flask(__name__)
app.debug=CONFIG.DEBUG
app.logger.setLevel(logging.DEBUG)
app.secret_key=CONFIG.SECRET_KEY

SCOPES = 'https://www.googleapis.com/auth/calendar.readonly'
CLIENT_SECRET_FILE = CONFIG.GOOGLE_KEY_FILE  ## You'll need this
APPLICATION_NAME = 'MeetMe class project'

try:
    dbclient = MongoClient(MONGO_CLIENT_URL)
    db = getattr(dbclient, CONFIG.DB)
    collection = db.meetings

except:
    print("Failure opening database.  Is Mongo running? Correct password?")
    sys.exit(1)

#############################
#
#  Pages (routed from URLs)
#
#############################

@app.route("/")
@app.route("/index")
def index():
  app.logger.debug("Index page entry")
  g.memos = get_memos()
  return flask.render_template('meeting.html')

@app.route("/choose")
def choose():
    ## We'll need authorization to list calendars 
    ## I wanted to put what follows into a function, but had
    ## to pull it back here because the redirect has to be a
    ## 'return' 
    app.logger.debug("Checking credentials for Google calendar access")
    credentials = valid_credentials()
    if not credentials:
      app.logger.debug("Redirecting to authorization")
      return flask.redirect(flask.url_for('oauth2callback'))

    gcal_service = get_gcal_service(credentials)
    app.logger.debug("Returned from get_gcal_service")
    flask.g.calendars = list_calendars(gcal_service)
    return render_template('index.html')

@app.route("/addMeeting")
def addMeeting():
    # Will need to add way to ask user for a unique name (check that is unique) and an optional password
    # Then user will be sent to a time select page (time will be saved into the db)
    # Free times will be found for the current user and will be saved ready for the other people to use
    app.logger.debug("Add page entered")
    return flask.render_template('addMeeting.html')

@app.route("/viewMeeting")
def viewMeeting():
    # Will take user to a screen asking for the meeting id and password. if given correctly it will display that page
    # User will be able to comment(The same way we added memos) only after they add their availability
    app.logger.debug("View page entered")
    return flask.render_template('viewMeeting.html')


@app.route("/_add_Meeting")
def addMemo():
    app.logger.debug("Checking credentials for Google calendar access")
    credentials = valid_credentials()
    if not credentials:
      app.logger.debug("Redirecting to authorization")
      return flask.redirect(flask.url_for('oauth2callback'))

    gcal_service = get_gcal_service(credentials)
    app.logger.debug("Returned from get_gcal_service")
    flask.g.calendars = list_calendars(gcal_service)

    app.logger.debug("Got a JSON request")
    title = request.form.get("title", type=str)
    pw = request.form.get("pw", type=str)
    comment = request.args.get("comment", type=str)

    record = {"name": title,
              'pw': hash_password(pw),
              "comments" : [ comment ],
              "dateRange" : [ arrow.get(flask.session['begin_date']).format("YYYY-MM-DD"),
                              arrow.get(flask.session['end_date']).format("YYYY-MM-DD") ],
              "timeRange" : [ arrow.get(flask.session['begin_time']).format("HH:mm"),
                              arrow.get(flask.session['end_time']).format("HH:mm") ],
              "freeTime" : flask.session["freeTime"]
              }
    print(record)
    collection.insert(record)
    return render_template('meeting.html')


def hash_password(password):
    # uuid is used to generate a random number
    salt = uuid.uuid4().hex
    return hashlib.sha256(salt.encode() + password.encode()).hexdigest() + ':' + salt


def check_password(hashed_password, user_password):
    password, salt = hashed_password.split(':')
    return password == hashlib.sha256(salt.encode() + user_password.encode()).hexdigest()

@app.route("/_delete")
def _delete():
    app.logger.debug("Starting to delete")
    post = request.args.get("delete", type=str)
    collection.remove({"_id": ObjectId(post)})
    return "Nothing"
####
#
#  Google calendar authorization:
#      Returns us to the main /choose screen after inserting
#      the calendar_service object in the session state.  May
#      redirect to OAuth server first, and may take multiple
#      trips through the oauth2 callback function.
#
#  Protocol for use ON EACH REQUEST: 
#     First, check for valid credentials
#     If we don't have valid credentials
#         Get credentials (jump to the oauth2 protocol)
#         (redirects back to /choose, this time with credentials)
#     If we do have valid credentials
#         Get the service object
#
#  The final result of successful authorization is a 'service'
#  object.  We use a 'service' object to actually retrieve data
#  from the Google services. Service objects are NOT serializable ---
#  we can't stash one in a cookie.  Instead, on each request we
#  get a fresh serivce object from our credentials, which are
#  serializable. 
#
#  Note that after authorization we always redirect to /choose;
#  If this is unsatisfactory, we'll need a session variable to use
#  as a 'continuation' or 'return address' to use instead. 
#
####

def valid_credentials():
    """
    Returns OAuth2 credentials if we have valid
    credentials in the session.  This is a 'truthy' value.
    Return None if we don't have credentials, or if they
    have expired or are otherwise invalid.  This is a 'falsy' value. 
    """
    if 'credentials' not in flask.session:
      return None

    credentials = client.OAuth2Credentials.from_json(
        flask.session['credentials'])

    if (credentials.invalid or
        credentials.access_token_expired):
      return None
    return credentials


def get_gcal_service(credentials):
  """
  We need a Google calendar 'service' object to obtain
  list of calendars, busy times, etc.  This requires
  authorization. If authorization is already in effect,
  we'll just return with the authorization. Otherwise,
  control flow will be interrupted by authorization, and we'll
  end up redirected back to /choose *without a service object*.
  Then the second call will succeed without additional authorization.
  """
  app.logger.debug("Entering get_gcal_service")
  http_auth = credentials.authorize(httplib2.Http())
  service = discovery.build('calendar', 'v3', http=http_auth)
  app.logger.debug("Returning service")
  return service

@app.route('/oauth2callback')
def oauth2callback():
  """
  The 'flow' has this one place to call back to.  We'll enter here
  more than once as steps in the flow are completed, and need to keep
  track of how far we've gotten. The first time we'll do the first
  step, the second time we'll skip the first step and do the second,
  and so on.
  """
  app.logger.debug("Entering oauth2callback")
  flow =  client.flow_from_clientsecrets(
      CLIENT_SECRET_FILE,
      scope= SCOPES,
      redirect_uri=flask.url_for('oauth2callback', _external=True))
  ## Note we are *not* redirecting above.  We are noting *where*
  ## we will redirect to, which is this function. 
  
  ## The *second* time we enter here, it's a callback 
  ## with 'code' set in the URL parameter.  If we don't
  ## see that, it must be the first time through, so we
  ## need to do step 1. 
  app.logger.debug("Got flow")
  if 'code' not in flask.request.args:
    app.logger.debug("Code not in flask.request.args")
    auth_uri = flow.step1_get_authorize_url()
    return flask.redirect(auth_uri)
    ## This will redirect back here, but the second time through
    ## we'll have the 'code' parameter set
  else:
    ## It's the second time through ... we can tell because
    ## we got the 'code' argument in the URL.
    app.logger.debug("Code was in flask.request.args")
    auth_code = flask.request.args.get('code')
    credentials = flow.step2_exchange(auth_code)
    flask.session['credentials'] = credentials.to_json()
    ## Now I can build the service and execute the query,
    ## but for the moment I'll just log it and go back to
    ## the main screen
    app.logger.debug("Got credentials")
    return flask.redirect(flask.url_for('choose'))

#####
#
#  Option setting:  Buttons or forms that add some
#     information into session state.  Don't do the
#     computation here; use of the information might
#     depend on what other information we have.
#   Setting an option sends us back to the main display
#      page, where we may put the new information to use. 
#
#####

@app.route('/setrange', methods=['POST'])
def setrange():
    """
    User chose a date range with the bootstrap daterange
    widget.
    """
    app.logger.debug("Entering setrange")  
    flask.flash("Setrange gave us '{}'".format(
      request.form.get('daterange')))
    daterange = request.form.get('daterange')
    start = request.form.get('start')
    end = request.form.get('end')
    print("PRINTING START END")
    print(interpret_time(start),interpret_time(end))
    flask.session['daterange'] = daterange
    daterange_parts = daterange.split()
    flask.session['begin_date'] = interpret_date(daterange_parts[0])
    flask.session['end_date'] = interpret_date(daterange_parts[2])
    flask.session["begin_time"] = interpret_time(start)
    flask.session["end_time"] = interpret_time(end)
    flask.session["start"] = start
    flask.session["end"] = end
    app.logger.debug("Setrange parsed {} - {}  dates as {} - {}".format(
      daterange_parts[0], daterange_parts[1], 
      flask.session['begin_date'], flask.session['end_date']))


    return addMemo()

    #return flask.redirect(flask.url_for("choose"))

####
#
#   Initialize session variables 
#
####

def init_session_values():
    """
    Start with some reasonable defaults for date and time ranges.
    Note this must be run in app context ... can't call from main. 
    """
    # Default date span = tomorrow to 1 week from now
    now = arrow.now('local')     # We really should be using tz from browser
    tomorrow = now.replace(days=+1)
    nextweek = now.replace(days=+7)
    flask.session["begin_date"] = tomorrow.floor('day').isoformat()
    flask.session["end_date"] = nextweek.ceil('day').isoformat()
    flask.session["daterange"] = "{} - {}".format(
        tomorrow.format("MM/DD/YYYY"),
        nextweek.format("MM/DD/YYYY"))
    # Default time span each day, 8 to 5

def interpret_time( text ):
    """
    Read time in a human-compatible format and
    interpret as ISO format with local timezone.
    May throw exception if time can't be interpreted. In that
    case it will also flash a message explaining accepted formats.
    """
    app.logger.debug("Decoding time '{}'".format(text))
    time_formats = ["ha", "h:mma",  "h:mm a", "H:mm"]
    try: 
        as_arrow = arrow.get(text, time_formats).replace(tzinfo=tz.tzlocal())
        as_arrow = as_arrow.replace(year=2016) #HACK see below
        app.logger.debug("Succeeded interpreting time")
    except:
        app.logger.debug("Failed to interpret time")
        flask.flash("Time '{}' didn't match accepted formats 13:30 or 1:30pm"
              .format(text))
        raise
    return as_arrow.isoformat()
    #HACK #Workaround
    # isoformat() on raspberry Pi does not work for some dates
    # far from now.  It will fail with an overflow from time stamp out
    # of range while checking for daylight savings time.  Workaround is
    # to force the date-time combination into the year 2016, which seems to
    # get the timestamp into a reasonable range. This workaround should be
    # removed when Arrow or Dateutil.tz is fixed.
    # FIXME: Remove the workaround when arrow is fixed (but only after testing
    # on raspberry Pi --- failure is likely due to 32-bit integers on that platform)


def interpret_date( text ):
    """
    Convert text of date to ISO format used internally,
    with the local time zone.
    """
    try:
      as_arrow = arrow.get(text, "MM/DD/YYYY").replace(
          tzinfo=tz.tzlocal())
    except:
        flask.flash("Date '{}' didn't fit expected format 12/31/2001")
        raise
    return as_arrow.isoformat()

def next_day(isotext):
    """
    ISO date + 1 day (used in query to Google calendar)
    """
    as_arrow = arrow.get(isotext)
    return as_arrow.replace(days=+1).isoformat()

####
#
#  Functions (NOT pages) that return some information
#
####
  
def list_calendars(service):
    """
    Given a google 'service' object, return a list of
    calendars.  Each calendar is represented by a dict.
    The returned list is sorted to have
    the primary calendar first, and selected (that is, displayed in
    Google Calendars web app) calendars before unselected calendars.
    """
    app.logger.debug("Entering list_calendars")
    calendar_list = service.calendarList().list().execute()["items"]
    result = [ ]
    for cal in calendar_list:
        kind = cal["kind"]
        cal_id = cal["id"]

        if "description" in cal:
            desc = cal["description"]
        else:
            desc = "(no description)"
        summary = cal["summary"]
        #print(cal['selected'])
        # Optional binary attributes with False as default
        selected = ("selected" in cal) and cal["selected"]
        primary = ("primary" in cal) and cal["primary"]
        events = list_events(service, cal_id)

        result.append(
          { "kind": kind,
            "id": cal_id,
            "summary": summary,
            "selected": selected,
            "primary": primary,
            "events": events
            })

    return sorted(result, key=cal_sort_key)


def list_events(service, cal_id):
    page_token = None
    begin = flask.session["begin_date"]
    end = flask.session["end_date"]
    beginTime = flask.session["begin_time"]
    endTime = flask.session["end_time"]
    min = arrow.get(begin).format('YYYY-MM-DD') + "T" + arrow.get(beginTime).format("HH:mm:ssZZ")
    #The reason I shifted 1 second here is because the max in non inclusive so I moved the time up to include the end
    #Time
    max = arrow.get(end).format('YYYY-MM-DD') + "T" + arrow.get(endTime).shift(seconds=+1).format("HH:mm:ssZZ")
    freeStart = min
    freeEnd = max
    while True:
        event_list = service.events().list(
                                            calendarId=cal_id,
                                            singleEvents=True,
                                            orderBy='startTime',
                                            pageToken=page_token,
                                            timeMin=min,
                                            timeMax=max).execute()

        result = get_next_free_time(min, max, event_list)

        page_token = event_list.get('nextPageToken')
        if not page_token:
            break

    return result


def get_next_free_time(start, end, event_list):
    # This is probably the worst possible way to do this but I really struggled to get this project finished
    # This ends up working but only because there are so many conditionals. Definitely not ideal but ill work
    # with it for now. If you could give me some pointers when you read over this that would be great!!
    # I annotated this as much as possible so there isnt much confusion on whats going on.
    start = arrow.get(start)
    end = arrow.get(end).shift(days=+1)
    relStart = start
    relEnd = arrow.get(arrow.get(start).format('YYYY-MM-DD') + "T" + arrow.get(end).format("HH:mm:ssZZ"))
    result = []
    dailyFreeTime = []
    freeTime = []

    for event in event_list['items']:
        print(relStart.format('MM/DD HH:mm'), relEnd.format('MM/DD HH:mm'))
        print(event['summary'], event['start'], event['end'])
        if 'dateTime' not in event['start']:
            print("skipping")
            continue
        eventStart = arrow.get(event['start']['dateTime'])
        eventEnd = arrow.get(event['end']['dateTime'])
        if (relStart.format('YYYY-MM-DD')) <= (eventStart.format('YYYY-MM-DD')):
            print(1)
            # If the relative day is not the same and the event day
            while(relStart.format('YYYY-MM-DD') < (eventStart.format('YYYY-MM-DD'))):
                print(relStart.format('HH:mm'), end.format('HH:mm'))
                if relStart.format('HH:mm') < end.format('HH:mm'):
                    print(2)
                    # If the relative start time is before the end the time the user give
                    result.append(addFreeTime(relStart.format('MM/DD HH:mm'), end.format('HH:mm')))
                    dailyFreeTime.append((relStart.format("HH:mm"), end.format('HH:mm')))
                freeTime.append(dailyFreeTime)
                print(dailyFreeTime)
                dailyFreeTime = []
                start = start.shift(days=+1)
                relStart = start
                relEnd = relEnd.shift(days=+1)
                print(relStart.format('MM/DD HH:mm'), relEnd.format('MM/DD HH:mm'))
        if relStart.format('HH:mm') < relEnd.format('HH:mm'):
            print(3)
            # If the relative start time is before the end time
            if relStart.isoformat() < eventStart.isoformat():
                print(4)
                if eventStart.isoformat() < relEnd.isoformat():
                    print(5)
                    # If the relative start time is before the event start
                    result.append(addFreeTime(relStart.format('MM/DD HH:mm'), eventStart.format('HH:mm')))
                    dailyFreeTime.append(relStart.format("HH:mm"), eventStart.format('HH:mm'))
                else:
                    print(6)
                    result.append(addFreeTime(relStart.format('MM/DD HH:mm'), relEnd.format('HH:mm')))
                    dailyFreeTime.append((relStart.format("HH:mm"), relEnd.format('HH:mm')))
            elif eventEnd.isoformat() < start.isoformat():
                print(7)
                # If the event ends before the user given start we dont care
                continue
            if eventEnd.format('HH:mm') < relEnd.format('HH:mm'):
                print(8)
                # If the event ends at the same time or before the user given end time
                relStart = eventEnd
            else:
                print(9)
                # The time ends after the user given end time
                relStart = relEnd

        event['readStart'] = arrow.get(event['start']['dateTime']).format("MM/DD HH:mm")
        event['readEnd'] = arrow.get(event['end']['dateTime']).format("HH:mm")
        if "transparency" not in event:
            result.append(event)
        print(relStart.format('MM/DD HH:mm'), relEnd.format('MM/DD HH:mm'))

    print(freeTime)
    flask.session["freeTime"] = freeTime
    return result



def addFreeTime(start, end):
    return(
        {
            'summary': 'Free time',
            'readStart': start.format('HH:mm'),
            'readEnd': end.format('HH:mm')
        })


def cal_sort_key( cal ):
    """
    Sort key for the list of calendars:  primary calendar first,
    then other selected calendars, then unselected calendars.
    (" " sorts before "X", and tuples are compared piecewise)
    """
    if cal["selected"]:
       selected_key = " "
    else:
       selected_key = "X"
    if cal["primary"]:
       primary_key = " "
    else:
       primary_key = "X"
    return (primary_key, selected_key, cal["summary"])

def get_memos():
    """
    Returns all memos in the database, in a form that
    can be inserted directly in the 'session' object.
    """
    records = [ ]
    for record in collection.find():
        print(record['dateRange'])
        print(record['timeRange'])
        records.append(record)
    return records

@app.template_filter( 'humanize' )
def humanize_arrow_date( date ):
    """
    Date is internal UTC ISO format string.
    Output should be "today", "yesterday", "in 5 days", etc.
    Arrow will try to humanize down to the minute, so we
    need to catch 'today' as a special case.

    UPDATE:
    This commit was made after the deadline I was just trying to see why this was not working.
    The date that is displayed is one day off so I hard coded in the values to try to fix that
    temporarily.
    """
    try:
        then = arrow.get(date).to('local')
        now = arrow.utcnow().to('local')
        if then.date() == arrow.get(arrow.now().shift(days=-1).isoformat()).date():
            human = "Today"
        elif then.date() == arrow.get(arrow.now().isoformat()).date():
            human = "Tomorrow"
        elif then.date() == arrow.get(arrow.now().isoformat()).shift(days=-2).date():
            human = "Yesterday"
        else:
            human = then.humanize(now)
    except:
        human = date
    return human




#################
#
# Functions used within the templates
#
#################

@app.template_filter( 'fmtdate' )
def format_arrow_date( date ):
    try: 
        normal = arrow.get( date )
        return normal.format("ddd MM/DD/YYYY")
    except:
        return "(bad date)"

@app.template_filter( 'fmttime' )
def format_arrow_time( time ):
    try:
        normal = arrow.get( time )
        return normal.format("HH:mm")
    except:
        return "(bad time)"
    
#############


if __name__ == "__main__":
  # App is created above so that it will
  # exist whether this is 'main' or not
  # (e.g., if we are running under green unicorn)
  app.run(port=CONFIG.PORT,host="0.0.0.0")
    

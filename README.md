# proj10-MeetMe

## Overview
This is my final project for CIS 322. This is still a work in progress and is a little rough around the edges. I am in the process of finishing this and will have it done by the end of this weekend. The way this project works is that it allows a user to create a meeting with a title and a password. The user will give a date range they want to have the meeting and the meeting will be added to mongodb. The user needs to give the title and password to any other people who want to be apart of this meeting as well. When they join the meeting they will add their free time to the database and the server will find similar times for them to meet. Finally all users are able to leave comments in the meetings page in case something does not work out and they want to change the day.


## How to use
I did not include my client ID or secrets so you will have to include your own
Create a credentials.ini file following this format
```
[DEFAULT]
DEBUG = True
author = Justin Robles
repo = https://github.com/Jrobles70/proj6-mongo
DB=
DB_USER=
DB_USER_PW=
ADMIN_USER=
ADMIN_PW=
DB_HOST=
DB_PORT=
PORT=
SECRET_KEY =
GOOGLE_KEY_FILE =
CLIENT_SECRET=
```

Using command like run
```
mongod
```
in a seperate command line run
```
make run
```
# This is not yet finished and will be finished by the end of this weekend!

## Authors

Initial version by M Young;
Revised by Justin Robles jrobles@uoregon.edu


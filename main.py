#!/usr/bin/env python

"""
main.py -- Udacity conference server-side Python App Engine
    HTTP controller handlers for memcache & task queue access

$Id$

created by wesc on 2014 may 24

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'

import webapp2
from google.appengine.api import app_identity
from google.appengine.api import mail
from google.appengine.api import memcache
from conference import ConferenceApi
from models import Session
import time

MEMCACHE_SPEAKER_KEY = "FEATURED_SPEAKER_"

class SetAnnouncementHandler(webapp2.RequestHandler):
    def get(self):
        """Set Announcement in Memcache."""
        # TODO 1
        ConferenceApi._cacheAnnouncement()


class SendConfirmationEmailHandler(webapp2.RequestHandler):
    def post(self):
        """Send email confirming Conference creation."""
        mail.send_mail(
            'noreply@%s.appspotmail.com' % (
                app_identity.get_application_id()),     # from
            self.request.get('email'),                  # to
            'You created a new Conference!',            # subj
            'Hi, you have created a following '         # body
            'conference:\r\n\r\n%s' % self.request.get(
                'conferenceInfo')
        )

class FeaturedSpeakerHandler(webapp2.RequestHandler):
    def post(self):
        """Check if speaker is at any other sessions from
        the same conference, set speaker and session names
        in memcache"""

        speaker = self.request.get('speaker')
        wsck = self.request.get('wsck')

        # Camacho - inserted this delay because the query was being run too
        # fast to pickup the session that was just entered
        time.sleep(3)
        # Camacho - check if the speaker has another session at the same conference
        q = Session.query().\
            filter(Session.speaker==speaker).\
            filter(Session.wsck==wsck)
        sessions = q.fetch(100)
        # Camacho - if more than one session is found, add a memcache entry that
        # features the speaker and session names
        if len(sessions) > 1:
            # Camacho - need a key specific to each conference since each one
            # could have a different featured speaker
            key = MEMCACHE_SPEAKER_KEY + wsck
            value = 'Speaker: ' + speaker + '. Sessions: '
            for sess in sessions:
                value = value + sess.name + ', '
            memcache.set(key,value[:-2])


app = webapp2.WSGIApplication([
    ('/crons/set_announcement', SetAnnouncementHandler),
    ('/tasks/send_confirmation_email', SendConfirmationEmailHandler),
    ('/tasks/check_session_speaker', FeaturedSpeakerHandler),
], debug=True)

#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import StringMessage
from models import Session
from models import SessionForm
from models import SessionForms
from models import Wishlist
from models import WishlistForm

from utils import getUserId

from settings import WEB_CLIENT_ID

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_SPEAKER_KEY = "FEATURED_SPEAKER_"

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_TYPE_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2),
)

SESS_SPEAK_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1),
)

WISH_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    sessionKeys=messages.StringField(1),
)

SESS_INFO_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    wssk = messages.StringField(1),
)

SPEAK_SESS_QUERY = endpoints.ResourceContainer (
    message_types.VoidMessage,
    speaker=messages.StringField(1),
    sessionType=messages.StringField(2),
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', 
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        # TODO 2: add confirmation email sending task to queue
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id =  getUserId(user)
        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )

            w_id = Wishlist.allocate_ids(size=1, parent=p_key)[0]
            w_key = ndb.Key(Wishlist, w_id, parent=p_key)

            wishlist = Wishlist(
                key = w_key,
                sessionKeys = []
            )

            profile.put()
            wishlist.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        # TODO 1
        # return an existing announcement from Memcache or an empty string.
        announcement = ""
        return StringMessage(data=announcement)

    @endpoints.method(CONF_GET_REQUEST,SessionForms,
            path='conferences/{websafeConferenceKey}',
            http_method='GET',name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return sessions for a particular conference."""
        wsck = request.websafeConferenceKey
        # create ancestor query for all key matches for this user
        sessions = Session.query(ancestor=ndb.Key(urlsafe=wsck))
        return SessionForms(
                items=[self._copySessionToForm(sess) for sess in \
                sessions]
        )

    def _copySessionToForm(self, sess):
        """Copy relevant fields from Session to SessionForm."""
        se = SessionForm()
        for field in se.all_fields():
            if hasattr(sess, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('date') or field.name.endswith('starttime'):
                    setattr(se, field.name, str(getattr(sess, field.name)))
                else:
                    setattr(se, field.name, getattr(sess, field.name))
        se.check_initialized()
        return se

    @endpoints.method(SessionForm, SessionForm, path='session',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new conference session."""
        return self._createSessionObject(request)

    def _createSessionObject(self, request):
        """Create or update Conference Session object, returning SessionForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # Camacho - make sure a name was entered for the session
        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")

        # Get the conference object from the wsck input
        try:
            wsck = request.wsck
            conf = ndb.Key(urlsafe=wsck).get()
        except:
            raise endpoints.BadRequestException("Invalid 'wsck' value")

        # Camacho - This will validate if the user created the conference to which
        # he or she is adding the session
        if user_id != conf.organizerUserId:
            raise endpoints.UnauthorizedException('You are not the creator of this conference')

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # convert dates from strings to Date objects; set month based on start_date
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()

        # Camacho - convert string times into time objects
        if data['starttime']:
            data['starttime'] = datetime.strptime(data['starttime'][:5], "%H:%M").time()

        # Camacho - get the conference key, create the session
        # key by inputting the conference key as its parent
        c_key = conf.key
        s_id = Session.allocate_ids(size=1, parent=c_key)[0]
        s_key = ndb.Key(Session, s_id, parent=c_key)
        data['key'] = s_key

        Session(**data).put()
        # Camacho - after adding the session, add a task to the queue to
        # check if the session's speaker should be a featured speaker
        if data['speaker']:
            taskqueue.add(params={
                'speaker': data['speaker'],
                'wsck':data['wsck']},
                url='/tasks/check_session_speaker'
            )
        return request


    @endpoints.method(SESS_TYPE_REQUEST,SessionForms,
            path='getConferenceSessionsByType',
            http_method='GET',name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Return sessions for a particular conference of a particular type."""
        wsck = request.websafeConferenceKey
        type = request.typeOfSession
        # create ancestor query for all key matches for this user
        sessions = Session.query(ancestor=ndb.Key(urlsafe=wsck))
        sessions = sessions.filter(Session.typeOfSession == type)
        return SessionForms(
                items=[self._copySessionToForm(sess) for sess in \
                sessions]
        )

    @endpoints.method(SESS_SPEAK_REQUEST,SessionForms,
            path='getSessionsBySpeaker',
            http_method='GET',name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Return sessions for a particular speaker across all conferences."""
        speak = request.speaker
        # Camacho - filter sessions by requested speaker
        sessions = Session.query()
        sessions = sessions.filter(Session.speaker == speak)
        return SessionForms(
                items=[self._copySessionToForm(sess) for sess in \
                sessions]
        )

    @endpoints.method(WISH_POST_REQUEST, SessionForms,
            path='addSessionToWishlist',
            http_method='PUT', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Update wishlist return w/updated info."""
        return self._addToWishlistObject(request)

    def _addToWishlistObject(self, request):
        """Add a session to a wishlist, return a wishlist form"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # Camacho - get the user's wishlist
        wl = Wishlist.query(ancestor=ndb.Key(Profile,user_id))
        wl = wl.get()

        # Camacho - make sure the wishlist exists
        if not wl:
            raise endpoints.NotFoundException(
                'Your user does not have a wishlist!')

        # Camacho - save the current keys
        try:
            currKeys = wl.sessionKeys
        except:
            currKeys = []

        # Camacho - get the key specified to add
        newKey = getattr(request,'sessionKeys')

        # Camacho - make sure the key specified corresponds
        # to an existing session
        try:
            session = ndb.Key(urlsafe=newKey).get()
        except:
            raise endpoints.NotFoundException(
                'Session with this key not found %s' % (newKey))

        currKeys.append(newKey)
        setattr(wl, 'sessionKeys', currKeys)

        wl.put()

        sessions = []

        # Pass a list of the sessions in the wishlist to the
        # _copySessionToForm function to return SessionForm
        # object
        for k in currKeys:
            sessions.append(ndb.Key(urlsafe=k).get())

        return SessionForms(
                items=[self._copySessionToForm(sess) for sess in \
                sessions]
        )

    def _copyWishlistToForm(self,wl):
        """Copy wishlist to Form object"""
        wlForm = WishlistForm()
        for field in wlForm.all_fields():
            if hasattr(wl, field.name):
                setattr(wlForm, field.name, getattr(wl, field.name))

        wlForm.check_initialized()
        return wlForm

    @endpoints.method(message_types.VoidMessage,WishlistForm,
            path='getSessionsInWishlist',
            http_method='GET',name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Return sessions for a particular speaker across all conferences."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # Camacho - get the user's wishlist
        wl = Wishlist.query(ancestor=ndb.Key(Profile,user_id)).get()

        # Camacho - make sure the wishlist exists
        if not wl:
            raise endpoints.NotFoundException(
                'Your user does not have a wishlist!')
        return self._copyWishlistToForm(wl)

    @endpoints.method(WISH_POST_REQUEST,WishlistForm,
            path='deleteSessionInWishlist',
            http_method='DELETE',name='deleteSessionInWishlist')
    def deleteSessionInWishlist(self, request):
        """Delete a single session in the user's wishlist."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # Camacho - Fetch the user's wishlist
        wl = Wishlist.query(ancestor=ndb.Key(Profile,user_id))
        wl = wl.get()

        wlForm = WishlistForm()

        # Camacho - check that conference exists
        if not wl:
            raise endpoints.NotFoundException(
                'Your user does not have a wishlist!')

        # Camacho - save the current keys
        try:
            currKeys = wl.sessionKeys
        except:
            currKeys = []

        # Camacho - get the requested key to delete
        delKey = getattr(request,'sessionKeys')

        # Camacho - make sure the requested key exists
        # in the user's wishlist
        try:
            currKeys.remove(delKey)
        except:
            raise endpoints.NotFoundException(
                'Session key not found in wishlist %s' % (delKey))

        setattr(wl, 'sessionKeys', currKeys)

        wl.put()

        return self._copyWishlistToForm(wl)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',http_method='GET',
            name='filterPlayground')
    def filterPlayground(self, request):
        """Return filtered conference results"""

        q = Session.query(ndb.OR(Session.typeOfSession=='lecture',\
                         Session.typeOfSession=='workshop')).\
                    filter(Session.starttime<'19:00')

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )

    @endpoints.method(SPEAK_SESS_QUERY, SessionForms,
            path='speakerSessQuery',http_method='GET',
            name='speakerSessQuery')
    def speakerSessQuery(self, request):
        """Return the  sessions of a specific speaker
           and a specific type of session"""

        speaker = request.speaker
        sessType = request.sessionType

        q = Session.query().\
            filter(Session.speaker == speaker).\
            filter(Session.typeOfSession == sessType)

        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in q]
        )

    @endpoints.method(SESS_INFO_REQUEST, StringMessage,
            path='numWishConfsQuery',http_method='GET',
            name='numWishConfsQuery')
    def numWishConfsQuery(self, request):
        """Return number of users who have a particular
           session in their wishlist"""

        wssk = request.wssk

        q = Wishlist.query()

        relevantWls = []

        for wlist in q:
            if wssk in wlist.sessionKeys:
                relevantWls.append(wlist)

        if relevantWls:
            if len(relevantWls) == 1:
                return StringMessage(data='There is 1 wishlist with this session')
            else:
                message = 'There are ' + str(len(relevantWls)) + ' wishlists with this session'
                return StringMessage(data=message)
        else:
            return StringMessage(data='There are no wishlists with this session')

    @endpoints.method(CONF_GET_REQUEST,StringMessage,
        path='getFeaturedSpeaker',http_method='GET',
        name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self,request):
        """Check if speaker is at any other sessions from
        the same conference, set speaker and session names
        in memcache"""
        key = MEMCACHE_SPEAKER_KEY + request.websafeConferenceKey
        data = memcache.get(key)
        if data:
            return StringMessage(data=repr(data))
        else:
            return StringMessage(data='No featured speaker')


api = endpoints.api_server([ConferenceApi]) # register API

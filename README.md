Alex Camacho - 12/29/15

This is for PROJECT 4 of the Udacity Full Stack Nanodegree

## TASK 1

I created the Session and SessionForm classes in the models file
and made sure most of the endpoints functions return the SessionForm
object.

When creating a session through the API explorer, the user will have
the choice to enter any of the fields available, but they MUST enter
the session's name and the websafe key of the conference to which
they want to link the session.  The websafe key can be found in the
details of the conference in the datastore, or in the entity details
of the admin site if deploying locally.  Once created, the session
will have the conference's key registered as its ancestor, thus
making the ability to search for all sessions of a particular
conference very easy.

getSessionsBySpeaker and getConferenceSessionsByType both only
require the one input their names imply (speaker, type of
conference).

I decided to make speakers just a string value in the Session class,
feeling that having a whole other class just for speakers was
needless.

## TASK 2

A user's wishlist will be created as soon as they log into the site.

Adding/Deleting sessions in a user's wishlist only requires the
entity key of the session they would like to add or delete, found
either in the datastore or the admin site if deploying locally.

## TASK 3

I ensured the appropriate indexes were available by first testing
the site locally, then deploying to appspot.

Extra query 1:
It is conceivable that a user that's a fan of a particular speaker
would only want to attend a certain type of session, a lecture for
example.  The code for this query would look like this:

q = Session.query().\
    filter(Session.speaker=='Jon Stewart').\
    filter(Session.typeOfSession=='workshop')

Extra query 2:
For the purposes of the site administrator, it might be interesting
to run a query on how many users have a particular session in
their wishlists, to see which are the most popular.  The session's
websafe key would be needed.  After obtaining the key, the code for
such a query would look like this:

wssk = websafeSessionKey

q = Wishlist.query().\
    filter(Wishlist.sessionKeys==wssk)

return len(q)

The main problem with a query to get all non-workshop pre-7pm
sessions is that it implies filters based on inequalities of more
than one property, typeOfSession and starttime.  To get around this,
multiple == statements can be combined with ORs like so to specify
all non-workshop sessions:

q = Session.query(ndb.OR(Session.typeOfSession=='lecture',\
                         Session.typeOfSession=='signing')).\
    filter(Session.starttime<'19:00')


---------- TASK 4 ----------
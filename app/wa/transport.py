"""The one place an outbound WhatsApp client is built (TASK-116), and which rail it is built for (TASK-117).

Every send path used to call ``M.Client()`` itself, so the Cloud API was wired into seven call
sites. They all go through ``get_client`` now: which transport runs is named by the thread's own
rail, and a second transport (the phone rail, ``app/wa/bridge.py``) needs no caller to change.

``client=`` still wins: ``cl = client or M.Client()`` became ``cl = T.get_client(client=client)``,
so an injected client -- every test's FakeMeta, the one ``campaign.py`` holds for a whole run, the
one ``api.process_phones`` resolves per phone and threads through the turn -- is handed straight
back. The test is ``is not None``, not truthiness: a caller that passes a client gets that client,
never a live one built behind its back.

THE RAIL IS PER THREAD, NOT PER PROCESS (TASK-117). ``wa_threads.rail`` is pinned on a thread's first
successful outbound (``store.pin_rail``) and never changes, because a rail is a sender number: the
Meta rail writes from the WABA number, the bridge rail from the number on the handset. So
``rail_for`` reads that column first and only an unpinned thread follows ``C.TRANSPORT`` -- flipping
that variable moves new conversations, never live ones.

A caller with no phone at all still resolves on ``C.TRANSPORT``: ``campaign.py:1091``/``:1135`` (one
run spans many numbers, and it opens a per-attempt turn on the client it holds) and
``import_history.py:534`` (a media fetch). Both are Meta-rail calls today; a bridge campaign resolves
its rail per recipient through the same function (TASK-127).
"""
from . import bridge as BR
from . import config as C
from . import meta as M
from . import store as ST


def rail_for(conn=None, phone=None):
    """-> the rail a send to ``phone`` goes out on: its thread's pinned rail, else ``C.TRANSPORT``.

    ``conn`` is the caller's open connection when it has one (``api._send`` sends and pins under the
    same connection and lock); without one a fresh read-only lookup is made. A phone with no thread
    row, or a thread that has never sent, is unpinned -- the first successful outbound decides.
    """
    if not phone:
        return C.TRANSPORT
    if conn is not None:
        return ST.rail_of(conn, phone) or C.TRANSPORT
    with ST.db() as c:
        return ST.rail_of(c, phone) or C.TRANSPORT


def rail_of_client(client):
    """-> the rail this client object actually is, or 'unknown' (TASK-146).

    Not the same question as ``rail_for``, which answers "which rail would a send to this phone go
    out on". An injected client -- every test's FakeMeta, the one ``campaign.py`` holds for a whole
    run -- can be a different rail from the thread's pinned one, and an error message that reports
    the thread's rail while holding the other client sends its reader to the wrong machine.
    """
    if isinstance(client, BR.Client):
        return "bridge"
    if isinstance(client, M.Client):
        return "meta"
    return "unknown"


def build(rail, **kw):
    """-> a client for this rail. ``**kw`` reaches the transport's constructor."""
    if rail == "meta":
        return M.Client(**kw)
    if rail == "bridge":
        return BR.Client(**kw)
    raise RuntimeError(f"{rail!r} is not a WhatsApp rail -- expected one of {', '.join(ST.RAILS)}")


def get_client(phone=None, client=None, conn=None, **kw):
    """-> the client this send goes through. ``**kw`` reaches the transport's constructor.

    ``client`` is returned as given, whatever it is: the caller already decided, and silently
    replacing it with a live Meta client would send a real message a test meant to fake.
    """
    if client is not None:
        return client
    return build(rail_for(conn, phone), **kw)

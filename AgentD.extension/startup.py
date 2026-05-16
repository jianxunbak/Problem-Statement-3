# -*- coding: utf-8 -*-
"""AgentD extension startup hook.

Runs once when pyRevit loads the AgentD extension at Revit launch. Auto-starts
the HTTP bridge listener (http://127.0.0.1:8101/) so Agent A can call us
without the user clicking the Start Bridge ribbon button first.

CRITICAL: any uncaught exception here will block the AgentD extension from
loading, hiding all our ribbon buttons. Every code path is wrapped.
"""
import os
import sys
import traceback


def _log(msg):
    try:
        print("[AgentD startup] {}".format(msg))
    except Exception:
        pass


def _find_bridge_dir(ext_root):
    """Locate Bridge.pushbutton under whichever .tab folder it lives in.

    Spec assumes AgentD.tab/, but this repo uses AgentAiD.tab/. Walk one level
    deep so we work regardless of which tab name the repo currently ships.
    """
    try:
        for tab_name in os.listdir(ext_root):
            if not tab_name.endswith(".tab"):
                continue
            tab_path = os.path.join(ext_root, tab_name)
            if not os.path.isdir(tab_path):
                continue
            for panel_name in os.listdir(tab_path):
                if not panel_name.endswith(".panel"):
                    continue
                bridge_path = os.path.join(tab_path, panel_name, "Bridge.pushbutton")
                if os.path.isdir(bridge_path):
                    return bridge_path
    except Exception:
        return None
    return None


def _autostart_bridge():
    try:
        ext_root = os.path.dirname(os.path.abspath(__file__))
        bridge_dir = _find_bridge_dir(ext_root)

        if not bridge_dir:
            # Bridge.pushbutton is not currently inside the extension (work in
            # progress, parked outside, etc.). This is not an error — autostart
            # has nothing to do. Stay silent so the pyRevit output isn't noisy.
            return

        if bridge_dir not in sys.path:
            sys.path.insert(0, bridge_dir)

        # Bridge.pushbutton/script.py is gated on __name__ == "__main__", so an
        # import does NOT auto-run the ribbon-click entry. We call start_bridge()
        # ourselves below.
        import script as _bridge_script  # imports Bridge.pushbutton/script.py

        uiapp = globals().get("__revit__")  # pyRevit injects this at startup
        ok = _bridge_script.start_bridge(uiapp)
        if ok:
            _log("bridge auto-started on http://127.0.0.1:8101")
        else:
            _log("bridge auto-start returned False — user can click Start Bridge manually")

    except Exception as e:
        # NEVER let this propagate — would block the whole extension from loading
        _log("autostart failed (extension still loaded, click Start Bridge manually): {}".format(e))
        _log(traceback.format_exc())


# Belt-and-braces: wrap the top level too.
try:
    _autostart_bridge()
except Exception:
    pass

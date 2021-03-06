# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
import ida_idp
import ida_kernwin
import ida_netnode

from .hooks import HexRaysHooks, Hooks, IDBHooks, IDPHooks, UIHooks, ViewHooks
from ..module import Module
from ..shared.commands import JoinSession, LeaveSession, ListDatabases


class Core(Module):
    """
    This is the core module. It is responsible for interacting with the IDA
    kernel. It will handle hooking, sending, and replaying of user events.
    """

    NETNODE_NAME = "$ idarling"

    def __init__(self, plugin):
        super(Core, self).__init__(plugin)
        self._project = None
        self._database = None
        self._tick = 0

        self._idb_hooks = None
        self._idp_hooks = None
        self._hxe_hooks = None
        self._view_hooks = None
        self._ui_hooks = None

        self._ui_hooks_core = None
        self._idb_hooks_core = None
        self._hooked = False

    @property
    def project(self):
        return self._project

    @project.setter
    def project(self, project):
        self._project = project
        assert ".." not in self._project
        self.save_netnode()

    @property
    def database(self):
        return self._database

    @database.setter
    def database(self, database):
        self._database = database
        assert ".." not in self._database
        self.save_netnode()

    @property
    def tick(self):
        return self._tick

    @tick.setter
    def tick(self, tick):
        self._tick = tick
        self.save_netnode()

    def _install(self):
        # Instantiate the hooks
        self._idb_hooks = IDBHooks(self._plugin)
        self._idp_hooks = IDPHooks(self._plugin)
        self._hxe_hooks = HexRaysHooks(self._plugin)
        self._view_hooks = ViewHooks(self._plugin)
        self._ui_hooks = UIHooks(self._plugin)

        core = self
        self._plugin.logger.debug("Installing core hooks")

        class UIHooksCore(Hooks, ida_kernwin.UI_Hooks):
            """
            The UI core hook is used to determine when IDA is fully loaded,
            meaning that we can starting hooking to receive our user events.
            """

            def __init__(self, plugin):
                ida_kernwin.UI_Hooks.__init__(self)
                Hooks.__init__(self, plugin)

            def ready_to_run(self, *_):
                self._plugin.logger.debug("Ready to run hook")
                core.load_netnode()
                core.join_session()
                self._plugin.interface.painter.set_custom_nav_colorizer()

            def database_inited(self, *_):
                self._plugin.logger.debug("Database inited hook")
                self._plugin.interface.painter.install()

        self._ui_hooks_core = UIHooksCore(self._plugin)
        self._ui_hooks_core.hook()

        class IDBHooksCore(Hooks, ida_idp.IDB_Hooks):
            """
            The IDB core hook is used to know when the database is being
            closed. We the need to unhook our user events.
            """

            def __init__(self, plugin):
                ida_idp.IDB_Hooks.__init__(self)
                Hooks.__init__(self, plugin)

            def closebase(self):
                self._plugin.logger.debug("Closebase hook")
                self._plugin.interface.painter.uninstall()
                core.leave_session()
                core.save_netnode()

                core.project = None
                core.database = None
                core.ticks = 0
                return 0

        self._idb_hooks_core = IDBHooksCore(self._plugin)
        self._idb_hooks_core.hook()
        return True

    def _uninstall(self):
        self._plugin.logger.debug("Uninstalling core hooks")
        self._idb_hooks_core.unhook()
        self._ui_hooks_core.unhook()
        self.unhook_all()
        return True

    def hook_all(self):
        """Install all the user events hooks."""
        if self._hooked:
            return

        self._plugin.logger.debug("Installing hooks")
        self._idb_hooks.hook()
        self._idp_hooks.hook()
        self._hxe_hooks.hook()
        self._view_hooks.hook()
        self._ui_hooks.hook()
        self._hooked = True

    def unhook_all(self):
        """Uninstall all the user events hooks."""
        if not self._hooked:
            return

        self._plugin.logger.debug("Uninstalling hooks")
        self._idb_hooks.unhook()
        self._idp_hooks.unhook()
        self._hxe_hooks.unhook()
        self._view_hooks.unhook()
        self._ui_hooks.unhook()
        self._hooked = False

    def load_netnode(self):
        """
        Load data from our custom netnode. Netnodes are the mechanism used by
        IDA to load and save information into a database. IDArling uses its own
        netnode to remember which project and database a database belongs to.
        """
        node = ida_netnode.netnode(Core.NETNODE_NAME, 0, True)

        self._project = node.hashval("project") or None
        assert ".." not in self._project
        self._database = node.hashval("database") or None
        assert ".." not in self._database
        self._tick = int(node.hashval("tick") or "0")

        self._plugin.logger.debug(
            "Loaded netnode: project=%s, database=%s, tick=%d"
            % (self._project, self._database, self._tick)
        )

    def save_netnode(self):
        """Save data into our custom netnode."""
        node = ida_netnode.netnode(Core.NETNODE_NAME, 0, True)

        if self._project:
            node.hashset("project", str(self._project))
        if self._database:
            node.hashset("database", str(self._database))
        if self._tick:
            node.hashset("tick", str(self._tick))

        self._plugin.logger.debug(
            "Saved netnode: project=%s, database=%s, tick=%d"
            % (self._project, self._database, self._tick)
        )

    def join_session(self):
        """Join the collaborative session."""
        self._plugin.logger.debug("Joining session")
        if self._project and self._database:

            def databases_listed(reply):
                if any(d.name == self._database for d in reply.databases):
                    self._plugin.logger.debug("Database is on the server")
                else:
                    self._plugin.logger.debug("Database is not on the server")
                    return  # Do not go further

                name = self._plugin.config["user"]["name"]
                color = self._plugin.config["user"]["color"]
                ea = ida_kernwin.get_screen_ea()
                self._plugin.network.send_packet(
                    JoinSession(
                        self._project,
                        self._database,
                        self._tick,
                        name,
                        color,
                        ea,
                    )
                )
                self.hook_all()

            d = self._plugin.network.send_packet(
                ListDatabases.Query(self._project)
            )
            if d:
                d.add_callback(databases_listed)
                d.add_errback(self._plugin.logger.exception)

    def leave_session(self):
        """Leave the collaborative session."""
        self._plugin.logger.debug("Leaving session")
        if self._project and self._database:
            name = self._plugin.config["user"]["name"]
            self._plugin.network.send_packet(LeaveSession(name))
            self.unhook_all()

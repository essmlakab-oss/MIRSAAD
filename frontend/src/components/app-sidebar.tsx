import { Link, useRouterState, useNavigate } from "@tanstack/react-router";
import { LogOut, Moon, ShieldAlert, Sun, Swords } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarFooter,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { Logo } from "@/components/logo";
import { logout } from "@/lib/auth";
import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

export function AppSidebar() {
  const { state } = useSidebar();
  const collapsed = state === "collapsed";
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();

  const isActive = (path: string) => pathname === path;

  async function signOut() {
    // The backend revokes the session and clears the cookies; there is no
    // client-side session state left to tidy up here.
    await logout();
    toast.success("Signed out");
    navigate({ to: "/auth" });
  }

  return (
    <Sidebar collapsible="icon" data-tour="app-sidebar">
      <SidebarHeader>
        <Link to="/" className="flex items-center gap-2 px-2 py-2">
          <Logo className="h-8 w-8" />
          {!collapsed && (
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold tracking-tight">MIRSAAD</div>
              <div className="truncate text-[11px] text-muted-foreground">
                Ground Truth Under Attack
              </div>
            </div>
          )}
        </Link>
      </SidebarHeader>

      <SidebarContent>
        {/* One console per analysis module. FDI detection is the first; the
            /dashboard stub redirects here so the next module can hang beside
            it without this menu being rewritten. */}
        <SidebarGroup>
          <SidebarGroupLabel>Analysis</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  asChild
                  isActive={isActive("/dashboard/fdi")}
                  tooltip="FDI Detection"
                >
                  <Link to="/dashboard/fdi">
                    <ShieldAlert className="h-4 w-4" />
                    <span>FDI Detection</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  asChild
                  isActive={isActive("/dashboard/arena")}
                  tooltip="Attack Arena"
                >
                  <Link to="/dashboard/arena">
                    <Swords className="h-4 w-4" />
                    <span>Attack Arena</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <div className="flex items-center gap-1 px-1">
          <Button variant="ghost" size="sm" className="flex-1 justify-start" onClick={toggle}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            {!collapsed && <span className="ml-2">{theme === "dark" ? "Light" : "Dark"}</span>}
          </Button>
          <Button variant="ghost" size="sm" onClick={signOut} aria-label="Sign out">
            <LogOut className="h-4 w-4" />
          </Button>
        </div>
      </SidebarFooter>
    </Sidebar>
  );
}

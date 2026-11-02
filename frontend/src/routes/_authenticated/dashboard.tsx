import { createFileRoute, Outlet } from "@tanstack/react-router";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/app-sidebar";
import { CopilotBubble } from "@/components/copilot-bubble";
import { SpotlightOverlay } from "@/components/spotlight-overlay";

export const Route = createFileRoute("/_authenticated/dashboard")({
  component: DashboardLayout,
});

function DashboardLayout() {
  return (
    <SidebarProvider>
      <div className="flex min-h-screen w-full bg-background text-foreground">
        <AppSidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border/60 bg-background/80 px-4 backdrop-blur">
            <SidebarTrigger />
            <div className="text-sm font-medium tracking-tight">
              MIRSAAD · Ground Truth Under Attack
            </div>
            <div className="ml-auto text-xs text-muted-foreground">FDI console</div>
          </header>
          <main className="flex-1 p-6">
            <Outlet />
          </main>
        </div>
        <CopilotBubble />
        {/* Portalled to <body>, so it dims the whole console rather than
            just the area inside this layout. */}
        <SpotlightOverlay />
      </div>
    </SidebarProvider>
  );
}

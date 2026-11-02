import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/_authenticated/dashboard/")({
  // The dashboard is one console per analysis module, and FDI detection is
  // the first module. A bare /dashboard lands here and goes straight to it;
  // the stub stays so the next module can hang beside it rather than fight
  // over this route.
  beforeLoad: () => {
    throw redirect({ to: "/dashboard/fdi" });
  },
  component: () => null,
});

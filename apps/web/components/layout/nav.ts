import type { IconName } from "@/components/ui/icons";

export type NavItem = { label: string; href: string; ready: boolean; icon: IconName; phase?: number };
export type NavGroup = { title: string; items: NavItem[] };

/** Sidebar sections (Part D4). `ready` flips to true as each phase ships the page. */
export const NAV_GROUPS: NavGroup[] = [
  {
    title: "Overview",
    items: [{ label: "Command Center", href: "/app/overview", ready: true, icon: "dashboard" }],
  },
  {
    title: "Plan",
    items: [
      { label: "Forecasts", href: "/app/forecasts", ready: true, icon: "chart" },
      { label: "Model Arena", href: "/app/arena", ready: true, icon: "trophy" },
      { label: "Scenarios", href: "/app/scenarios", ready: true, icon: "flask" },
    ],
  },
  {
    title: "Operate",
    items: [
      { label: "Inventory", href: "/app/inventory", ready: true, icon: "box" },
      { label: "Replenishment", href: "/app/replenishment", ready: true, icon: "truck" },
      { label: "Purchase Orders", href: "/app/purchase-orders", ready: true, icon: "layers" },
      { label: "POS", href: "/app/pos", ready: true, icon: "cart" },
    ],
  },
  {
    title: "Platform",
    items: [
      { label: "Data", href: "/app/data", ready: true, icon: "database" },
      { label: "Models", href: "/app/models", ready: true, icon: "cpu" },
      { label: "Jobs", href: "/app/jobs", ready: true, icon: "queue" },
      { label: "System", href: "/app/system", ready: true, icon: "activity" },
      { label: "Settings", href: "/app/settings", ready: true, icon: "sliders" },
    ],
  },
];

export const NAV: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

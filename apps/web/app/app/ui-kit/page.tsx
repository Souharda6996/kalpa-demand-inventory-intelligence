import { notFound } from "next/navigation";
import { UiKit } from "./ui-kit";

/** Dev-only component showcase (404 in production). Sample values below are labelled as samples. */
export default function UiKitPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <UiKit />;
}

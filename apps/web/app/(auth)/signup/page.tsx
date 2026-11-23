"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { AuthCard, buttonCls, Field, inputCls } from "@/components/layout/auth-card";
import { api } from "@/lib/api-client";

// Same rules as the API's SignupIn (Pydantic): password 10-128, names 1-200.
const schema = z.object({
  name: z.string().min(1, "Enter your name").max(200),
  org_name: z.string().min(1, "Enter an organisation name").max(200),
  email: z.email("Enter a valid email"),
  password: z.string().min(10, "At least 10 characters").max(128),
  currency: z.enum(["INR", "USD"]),
});
type Values = z.infer<typeof schema>;

export default function SignupPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { currency: "INR" } });

  async function onSubmit(values: Values) {
    setFormError(null);
    const { data, error } = await api.POST("/api/v1/auth/signup", { body: values });
    if (!data) {
      setFormError((error as { message?: string } | undefined)?.message ?? "Could not create the account");
      return;
    }
    qc.setQueryData(["me"], data);
    router.push("/onboarding");
  }

  return (
    <AuthCard title="Create your workspace" subtitle="You become the owner of a new organisation.">
      <form onSubmit={handleSubmit(onSubmit)} noValidate>
        <Field label="Your name" error={errors.name?.message}>
          <input autoComplete="name" className={inputCls} {...register("name")} />
        </Field>
        <Field label="Organisation" error={errors.org_name?.message}>
          <input autoComplete="organization" className={inputCls} {...register("org_name")} />
        </Field>
        <Field label="Email" error={errors.email?.message}>
          <input type="email" autoComplete="email" className={inputCls} {...register("email")} />
        </Field>
        <Field label="Password" error={errors.password?.message}>
          <input type="password" autoComplete="new-password" className={inputCls} {...register("password")} />
        </Field>
        <Field label="Currency" error={errors.currency?.message}>
          <select className={inputCls} {...register("currency")}>
            <option value="INR">INR (₹)</option>
            <option value="USD">USD ($)</option>
          </select>
        </Field>
        {formError && (
          <p role="alert" className="mb-4 text-sm text-danger">
            {formError}
          </p>
        )}
        <button type="submit" disabled={isSubmitting} className={buttonCls}>
          {isSubmitting ? "Creating..." : "Create account"}
        </button>
      </form>
      <p className="mt-4 text-sm text-muted">
        Already have an account?{" "}
        <Link href="/login" className="text-primary underline">
          Sign in
        </Link>
      </p>
    </AuthCard>
  );
}

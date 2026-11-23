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

const schema = z.object({
  email: z.email("Enter a valid email"),
  password: z.string().min(1, "Enter your password"),
});
type Values = z.infer<typeof schema>;

export default function LoginPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema) });

  async function onSubmit(values: Values) {
    setFormError(null);
    const { data, error, response } = await api.POST("/api/v1/auth/login", { body: values });
    if (!data) {
      const msg = (error as { message?: string } | undefined)?.message;
      setFormError(response.status === 429 ? "Too many attempts. Try again in a few minutes." : (msg ?? "Sign-in failed"));
      return;
    }
    qc.setQueryData(["me"], data);
    router.push("/app/overview");
  }

  return (
    <AuthCard title="Sign in" subtitle="Welcome back to Kalpa.">
      <form onSubmit={handleSubmit(onSubmit)} noValidate>
        <Field label="Email" error={errors.email?.message}>
          <input type="email" autoComplete="email" className={inputCls} {...register("email")} />
        </Field>
        <Field label="Password" error={errors.password?.message}>
          <input type="password" autoComplete="current-password" className={inputCls} {...register("password")} />
        </Field>
        {formError && (
          <p role="alert" className="mb-4 text-sm text-danger">
            {formError}
          </p>
        )}
        <button type="submit" disabled={isSubmitting} className={buttonCls}>
          {isSubmitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
      <p className="mt-4 text-sm text-muted">
        New here?{" "}
        <Link href="/signup" className="text-primary underline">
          Create an account
        </Link>
      </p>
    </AuthCard>
  );
}

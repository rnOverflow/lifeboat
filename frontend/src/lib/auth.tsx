import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken, type Me } from "./api";

interface AuthCtx {
  me: Me | null;
  loading: boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => void;
  can: (perm: string) => boolean;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(!!getToken());

  const refresh = useCallback(async () => {
    if (!getToken()) return setLoading(false);
    try {
      setMe(await api<Me>("/auth/me"));
    } catch {
      setToken(null);
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const onLogout = () => setMe(null);
    window.addEventListener("lifeboat:logout", onLogout);
    return () => window.removeEventListener("lifeboat:logout", onLogout);
  }, [refresh]);

  const login = async (username: string, password: string) => {
    const r = await api<{ access_token: string }>("/auth/login", { method: "POST", body: { username, password } });
    setToken(r.access_token);
    await refresh();
  };
  const logout = () => {
    setToken(null);
    setMe(null);
  };
  const can = (perm: string) => !!me?.permissions.includes(perm);
  return <Ctx.Provider value={{ me, loading, login, logout, can }}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}

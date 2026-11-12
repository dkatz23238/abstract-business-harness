import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  createUser,
  deleteUser,
  listUsers,
  rotateUserApiKey,
  updateUser,
} from "../adminApi";
import type { UserInfo, UserRole, UserVisibility } from "../api";
import Modal, { btnDanger, btnGhost, btnPrimary, inputClass, SecretInput } from "./Modal";

type Dialog =
  | { kind: "add" }
  | { kind: "password"; username: string }
  | { kind: "delete"; username: string }
  | { kind: "key"; username: string; key: string }
  | null;

const selectClass = "rounded-md border border-line bg-pane px-2 py-1 text-[14px] text-ink";

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Admin-only list of accounts: add, change role/visibility, reset passwords,
 *  rotate API keys, delete. */
export default function Users({ me }: { me: string }) {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);

  const [newName, setNewName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<UserRole>("user");
  const [newVisibility, setNewVisibility] = useState<UserVisibility>("siloed");
  const [resetPassword, setResetPassword] = useState("");
  const [purge, setPurge] = useState(false);
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(() => {
    listUsers()
      .then((list) => {
        setUsers(list);
        setError(null);
      })
      .catch((e) => setError(errText(e)));
  }, []);

  useEffect(refresh, [refresh]);

  const close = () => {
    setDialog(null);
    setNewName("");
    setNewPassword("");
    setNewRole("user");
    setNewVisibility("siloed");
    setResetPassword("");
    setPurge(false);
    setCopied(false);
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
      setError(null);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const add = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const { user, api_key } = await createUser({
        username: newName.trim(),
        password: newPassword,
        role: newRole,
        visibility: newVisibility,
      });
      close();
      setDialog({ kind: "key", username: user.username, key: api_key });
    });
  };

  const change = (username: string, body: { role?: UserRole; visibility?: UserVisibility }) =>
    run(async () => {
      await updateUser(username, body);
    });

  const rotate = (username: string) =>
    run(async () => {
      const key = await rotateUserApiKey(username);
      setDialog({ kind: "key", username, key });
    });

  const submitReset = (e: FormEvent) => {
    e.preventDefault();
    if (dialog?.kind !== "password") return;
    const username = dialog.username;
    run(async () => {
      await updateUser(username, { password: resetPassword });
      close();
    });
  };

  const confirmDelete = () => {
    if (dialog?.kind !== "delete") return;
    const { username } = dialog;
    const wipe = purge;
    run(async () => {
      await deleteUser(username, wipe);
      close();
    });
  };

  return (
    <section className="pane mx-auto w-full max-w-[960px] overflow-y-auto p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="m-0 text-[19px] font-semibold text-ink">Users</h2>
          <p className="m-0 text-[14px] text-muted">
            Each user has their own conversations, workspaces and API key. Shared users can also
            read everyone else&apos;s conversations; admins can do that and manage the profile.
          </p>
        </div>
        <button className={btnPrimary} onClick={() => setDialog({ kind: "add" })}>
          Add user
        </button>
      </div>
      {error && <p className="mb-3 text-[14px] text-danger">{error}</p>}
      <table className="w-full border-collapse text-[15px]">
        <thead>
          <tr className="border-b border-line text-left text-[13px] uppercase text-muted">
            <th className="py-2">Username</th>
            <th className="py-2">Role</th>
            <th className="py-2">Visibility</th>
            <th className="py-2">Created</th>
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const bootstrap = u.username === "admin";
            return (
              <tr key={u.username} className="border-b border-line">
                <td className="py-2 font-medium text-ink">
                  {u.username}
                  {u.username === me && <span className="ml-2 text-[13px] text-muted">(you)</span>}
                </td>
                <td className="py-2">
                  <select
                    className={selectClass}
                    value={u.role}
                    disabled={busy || bootstrap}
                    title={bootstrap ? "The admin account always keeps the admin role" : undefined}
                    onChange={(e) => change(u.username, { role: e.target.value as UserRole })}
                  >
                    <option value="user">user</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
                <td className="py-2">
                  <select
                    className={selectClass}
                    value={u.visibility}
                    disabled={busy || u.role === "admin"}
                    title={u.role === "admin" ? "Admins can always read every conversation" : undefined}
                    onChange={(e) =>
                      change(u.username, { visibility: e.target.value as UserVisibility })
                    }
                  >
                    <option value="siloed">siloed</option>
                    <option value="shared">shared (read others)</option>
                  </select>
                </td>
                <td className="py-2 text-muted">
                  {u.created_at ? new Date(u.created_at * 1000).toLocaleDateString() : ""}
                </td>
                <td className="py-2 text-right whitespace-nowrap">
                  <button
                    className={btnGhost}
                    disabled={busy}
                    onClick={() => setDialog({ kind: "password", username: u.username })}
                  >
                    Reset password
                  </button>{" "}
                  <button className={btnGhost} disabled={busy} onClick={() => rotate(u.username)}>
                    New API key
                  </button>{" "}
                  <button
                    className={btnDanger}
                    disabled={busy || bootstrap || u.username === me}
                    onClick={() => setDialog({ kind: "delete", username: u.username })}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <Modal
        open={dialog?.kind === "add"}
        title="Add user"
        onClose={close}
        footer={
          <>
            <button type="button" className={btnGhost} onClick={close}>
              Cancel
            </button>
            <button
              type="submit"
              form="add-user"
              className={btnPrimary}
              disabled={busy || !newName.trim() || newPassword.length < 8}
            >
              Create
            </button>
          </>
        }
      >
        <form id="add-user" className="flex flex-col gap-3" onSubmit={add}>
          <input
            className={inputClass}
            placeholder="username (lowercase letters, digits, - and _)"
            value={newName}
            autoComplete="off"
            onChange={(e) => setNewName(e.target.value.toLowerCase())}
          />
          <SecretInput value={newPassword} onChange={setNewPassword} placeholder="Password (8+ characters)" />
          <div className="flex gap-3">
            <label className="flex items-center gap-2 text-[14px] text-muted">
              Role
              <select
                className={selectClass}
                value={newRole}
                onChange={(e) => setNewRole(e.target.value as UserRole)}
              >
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </label>
            <label className="flex items-center gap-2 text-[14px] text-muted">
              Visibility
              <select
                className={selectClass}
                value={newVisibility}
                onChange={(e) => setNewVisibility(e.target.value as UserVisibility)}
              >
                <option value="siloed">siloed</option>
                <option value="shared">shared (read others)</option>
              </select>
            </label>
          </div>
        </form>
      </Modal>

      <Modal
        open={dialog?.kind === "password"}
        title={dialog?.kind === "password" ? `Reset password for ${dialog.username}` : ""}
        onClose={close}
        footer={
          <>
            <button type="button" className={btnGhost} onClick={close}>
              Cancel
            </button>
            <button
              type="submit"
              form="reset-password"
              className={btnPrimary}
              disabled={busy || resetPassword.length < 8}
            >
              Set password
            </button>
          </>
        }
      >
        <form id="reset-password" className="flex flex-col gap-2" onSubmit={submitReset}>
          <SecretInput value={resetPassword} onChange={setResetPassword} placeholder="New password (8+ characters)" />
          <p className="m-0 text-[14px] text-muted">This signs the user out everywhere. Their API key keeps working.</p>
        </form>
      </Modal>

      <Modal
        open={dialog?.kind === "delete"}
        title={dialog?.kind === "delete" ? `Delete ${dialog.username}?` : ""}
        onClose={close}
        footer={
          <>
            <button type="button" className={btnGhost} onClick={close}>
              Cancel
            </button>
            <button type="button" className={btnDanger} disabled={busy} onClick={confirmDelete}>
              Delete
            </button>
          </>
        }
      >
        <p className="mt-0">The account and its API key stop working immediately.</p>
        <label className="flex items-center gap-2 text-[14px]">
          <input type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} />
          Also delete their conversations and workspace files
        </label>
      </Modal>

      <Modal
        open={dialog?.kind === "key"}
        title={dialog?.kind === "key" ? `API key for ${dialog.username}` : ""}
        onClose={close}
        footer={
          <button type="button" className={btnPrimary} onClick={close}>
            Done
          </button>
        }
      >
        {dialog?.kind === "key" && (
          <>
            <p className="mt-0 text-[14px] text-muted">
              Shown once. Hand it to the user for scripts (<code>Authorization: Bearer …</code>);
              any previous key for this user no longer works.
            </p>
            <div className="flex gap-2">
              <input className={inputClass} readOnly value={dialog.key} onFocus={(e) => e.target.select()} />
              <button
                type="button"
                className={btnGhost}
                onClick={() => {
                  navigator.clipboard?.writeText(dialog.key).then(() => setCopied(true));
                }}
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </>
        )}
      </Modal>
    </section>
  );
}

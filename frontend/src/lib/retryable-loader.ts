/** Share an in-flight load and retain success, but allow another attempt after failure. */
export function createRetryableLoader<T>(load: () => Promise<T>): () => Promise<T> {
  let pending: Promise<T> | undefined;
  return () => {
    if (!pending) {
      pending = Promise.resolve().then(load).catch((error: unknown) => {
        pending = undefined;
        throw error;
      });
    }
    return pending;
  };
}

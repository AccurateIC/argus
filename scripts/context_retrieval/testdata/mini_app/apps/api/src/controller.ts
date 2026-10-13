import { greet } from "./service";

export function handle(): string {
  return greet("world");
}

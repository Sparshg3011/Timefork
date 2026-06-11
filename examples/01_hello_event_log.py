"""Hello, event log.

Creates a run, appends a few events to its diary, reads them back, and shows
the database refusing a duplicate sequence number.

Needs Postgres up first:  docker compose up -d --wait
Run with:                 python examples/01_hello_event_log.py
"""

from timefork.events import (
    DuplicateSequenceError,
    append_event,
    connect,
    create_run,
    read_events,
    set_run_status,
)


def main() -> None:
    with connect() as conn:
        # A run is one life of an agent; its diary starts empty.
        run_id = create_run(conn, "hello_agent", {"question": "what is durable execution?"})
        print(f"created run {run_id}")

        # The writer numbers each entry explicitly: 1, 2, 3, ...
        append_event(conn, run_id, 1, "RUN_STARTED", {"question": "what is durable execution?"})
        append_event(conn, run_id, 2, "LLM_CALLED", {"model": "mock-1", "response": "It means finishing what you started."})
        append_event(conn, run_id, 3, "RUN_COMPLETED", {"output": "It means finishing what you started."})
        print("appended events 1..3")

        print("\nthe diary so far:")
        for event in read_events(conn, run_id):
            print(f"  {event.seq}  {event.type:15} {event.payload}")

        # Slot 3 is already taken; the primary key rejects a second write.
        print("\ntrying to append seq 3 again...")
        try:
            append_event(conn, run_id, 3, "RUN_COMPLETED", {"output": "an impostor"})
        except DuplicateSequenceError as exc:
            print(f"  refused by the database: {exc}")

        # History is untouched: still exactly three entries.
        print(f"\ndiary still has {len(read_events(conn, run_id))} entries")

        set_run_status(conn, run_id, "completed")
        print("run marked completed")


if __name__ == "__main__":
    main()

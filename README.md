# MSC Chat Bot

Allows for interactive MSC metadata queries as well as a daily summary of current MSC status.

## Installing

Install dependencies:

```
pip3 install -r requirements.txt
```

Copy and customise config:

```
cp config.sample.toml config.toml
```

Run:

```
python3 main.py
```

## Commands

Commands are prefaced with `mscbot:`. Pills also work.

Syntax:

```
mscbot: <command>
```

### Available Commands

`show new` - Show MSCs that are still being finalized.

`show pending` - Show MSCs which are pending a FCP. These need review from team members.

`show fcp` - Show MSCs that are current in FCP.

`show all` - Combined response of all of the above.

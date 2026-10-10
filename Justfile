# Recipes by what you are doing, in the order you meet them. `just <module>`
# lists a module's recipes; docs/justfile-recipes.md explains them.

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

[private]
default:
    @just --list --unsorted

# Once per laptop and account: install, key, Functions, MCP
mod setup "just/setup.just"

# Approved messages: the opener and closing Meta reviews
mod approved "just/approved.just"

# Write a survey and build it into a flow
mod survey "just/survey.just"

# Put a flow on Twilio and look inside it
mod flow "just/flow.just"

# Send a round and watch it land
mod round "just/round.just"

# What came back: check, decrypt, reconcile, load
mod data "just/data.just"

# The shipped data-use demo flow
mod demo "just/demo.just"

# Maintaining the repo: tests, lint, format
mod dev "just/dev.just"

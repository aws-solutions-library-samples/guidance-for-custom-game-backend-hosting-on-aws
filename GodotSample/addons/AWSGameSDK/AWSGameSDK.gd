@tool
extends EditorPlugin

const AUTOLOAD_NAME = "AWSGameSDK"

static var AWSGameSDKAuth = preload("./scripts/AWSAuthorization.gd")
static var AWSGameSDKBackend = preload("./scripts/AWSBackend.gd")


func _enable_plugin():
	add_autoload_singleton(AUTOLOAD_NAME, "res://addons/AWSGameSDK/AWSAuthorization.gd")
	add_autoload_singleton(AUTOLOAD_NAME, "res://addons/AWSGameSDK/AWSBackend.gd")


func _disable_plugin():
	remove_autoload_singleton(AUTOLOAD_NAME)


func _init() -> void:
	pass

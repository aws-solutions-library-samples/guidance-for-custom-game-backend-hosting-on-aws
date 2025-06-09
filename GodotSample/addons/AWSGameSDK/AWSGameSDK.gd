@tool
extends EditorPlugin

const AUTOLOAD_NAME_SDK = "AWSGameSDK"
const AUTOLOAD_NAME_AUTH = "AWSGameSDKAuth"

static var AWSGameSDKAuth = preload("./scripts/AWSAuthorization.gd")
static var AWSGameSDKBackend = preload("./scripts/AWSBackend.gd")


func _enable_plugin():
	add_autoload_singleton(AUTOLOAD_NAME_AUTH, "res://addons/AWSGameSDK/AWSAuthorization.gd")
	add_autoload_singleton(AUTOLOAD_NAME_SDK, "res://addons/AWSGameSDK/AWSBackend.gd")


func _disable_plugin():
	remove_autoload_singleton(AUTOLOAD_NAME_SDK)
	remove_autoload_singleton(AUTOLOAD_NAME_AUTH)


func _init() -> void:
	pass
